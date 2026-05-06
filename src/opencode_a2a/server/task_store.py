from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from a2a.server.context import ServerCallContext
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task
from sqlalchemy.engine import make_url

from ..a2a_utils import proto_equals
from ..config import Settings
from ..task_states import TERMINAL_TASK_STATES
from .context_helpers import normalize_server_call_context
from .database import build_database_engine, redact_database_url_for_logs
from .task_store_sdk_compat import DatabaseTaskStoreCompat

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


class TaskStoreOperationError(RuntimeError):
    def __init__(self, operation: str, task_id: str | None) -> None:
        self.operation = operation
        self.task_id = task_id
        target = task_id or "unknown"
        super().__init__(f"Task store {operation} failed for task_id={target}")


@dataclass(frozen=True)
class TaskPersistenceDecision:
    persist: bool
    reason: str | None = None


class TaskWritePolicy(ABC):
    @abstractmethod
    def evaluate(
        self,
        *,
        existing: Task | None,
        incoming: Task,
    ) -> TaskPersistenceDecision: ...


class FirstTerminalStateWinsPolicy(TaskWritePolicy):
    """Treat terminal task snapshots as immutable once persisted."""

    def evaluate(
        self,
        *,
        existing: Task | None,
        incoming: Task,
    ) -> TaskPersistenceDecision:
        if existing is None or existing.status.state not in TERMINAL_TASK_STATES:
            return TaskPersistenceDecision(persist=True)
        if incoming.status.state != existing.status.state:
            return TaskPersistenceDecision(
                persist=False,
                reason="state_overwrite_after_terminal_persistence",
            )
        if not proto_equals(incoming, existing):
            return TaskPersistenceDecision(
                persist=False,
                reason="late_mutation_after_terminal_persistence",
            )
        return TaskPersistenceDecision(persist=True)


class TaskStoreDecorator(TaskStore):
    def __init__(self, inner: TaskStore) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def save(
        self,
        task: Task,
        context: ServerCallContext | None = None,
    ) -> None:
        await self._inner.save(task, _normalize_task_store_context(context))

    async def get(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        return await self._inner.get(task_id, _normalize_task_store_context(context))

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext | None = None,
    ) -> ListTasksResponse:
        return await self._inner.list(params, _normalize_task_store_context(context))

    async def delete(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> None:
        await self._inner.delete(task_id, _normalize_task_store_context(context))


class TaskStoreOperationWrappingDecorator(TaskStoreDecorator):
    async def save(
        self,
        task: Task,
        context: ServerCallContext | None = None,
    ) -> None:
        normalized_context = _normalize_task_store_context(context)
        try:
            await self._inner.save(task, normalized_context)
        except TaskStoreOperationError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("save", task.id) from exc

    async def get(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        normalized_context = _normalize_task_store_context(context)
        try:
            return await self._inner.get(task_id, normalized_context)
        except TaskStoreOperationError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("get", task_id) from exc

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext | None = None,
    ) -> ListTasksResponse:
        try:
            return await self._inner.list(params, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("list", None) from exc

    async def delete(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> None:
        normalized_context = _normalize_task_store_context(context)
        try:
            await self._inner.delete(task_id, normalized_context)
        except TaskStoreOperationError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("delete", task_id) from exc


class PolicyAwareTaskStore(TaskStoreDecorator):
    def __init__(
        self,
        inner: TaskStore,
        *,
        write_policy: TaskWritePolicy | None = None,
    ) -> None:
        super().__init__(inner)
        self._write_policy = write_policy or FirstTerminalStateWinsPolicy()
        self._save_lock = asyncio.Lock()
        self._atomic_guard_fallback_logged = False

    async def save(
        self,
        task: Task,
        context: ServerCallContext | None = None,
    ) -> None:
        normalized_context = _normalize_task_store_context(context)
        raw_task_store = unwrap_task_store(self._inner)
        if isinstance(raw_task_store, DatabaseTaskStore):
            await self._save_database_task(raw_task_store, task, normalized_context)
            return
        await self._save_with_read_before_write(task, normalized_context)

    async def _save_with_read_before_write(
        self,
        task: Task,
        context: ServerCallContext,
    ) -> None:
        async with self._save_lock:
            existing = await self._inner.get(task.id, context)
            decision = self._write_policy.evaluate(existing=existing, incoming=task)
            self._log_terminal_persistence_decision(
                existing=existing,
                incoming=task,
                decision=decision,
            )
            if not decision.persist:
                return
            await self._inner.save(task, context)

    async def _save_database_task(
        self,
        task_store: DatabaseTaskStore,
        task: Task,
        context: ServerCallContext,
    ) -> None:
        compat = DatabaseTaskStoreCompat(task_store)
        if not compat.supports_atomic_terminal_guard():
            if not self._atomic_guard_fallback_logged:
                logger.warning(
                    "Database-backed task store dialect does not support atomic terminal guard; "
                    "falling back to read-before-write policy dialect=%s",
                    compat.dialect_name,
                )
                self._atomic_guard_fallback_logged = True
            await self._save_with_read_before_write(task, context)
            return

        try:
            if await compat.atomic_save(task, context):
                return
            existing = await compat.load_task(task.id, context)
            decision = self._write_policy.evaluate(existing=existing, incoming=task)
            self._log_terminal_persistence_decision(
                existing=existing,
                incoming=task,
                decision=decision,
            )
            if not decision.persist:
                return
            if (
                existing is not None
                and existing.status.state in TERMINAL_TASK_STATES
                and proto_equals(existing, task)
            ):
                return
            raise RuntimeError(
                "Atomic task persistence was skipped without an authoritative terminal task."
            )
        except TaskStoreOperationError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("save", task.id) from exc

    def _log_terminal_persistence_decision(
        self,
        *,
        existing: Task | None,
        incoming: Task,
        decision: TaskPersistenceDecision,
    ) -> None:
        if existing is None or existing.status.state not in TERMINAL_TASK_STATES:
            return
        logger.warning(
            "Received task persistence after terminal state task_id=%s existing_state=%s "
            "incoming_state=%s persist=%s reason=%s",
            incoming.id,
            existing.status.state,
            incoming.status.state,
            decision.persist,
            decision.reason or "accepted_duplicate",
        )


class GuardedTaskStore(PolicyAwareTaskStore):
    def __init__(
        self,
        inner: TaskStore,
        *,
        write_policy: TaskWritePolicy | None = None,
    ) -> None:
        super().__init__(
            TaskStoreOperationWrappingDecorator(inner),
            write_policy=write_policy,
        )


@dataclass(slots=True)
class TaskStoreRuntime:
    task_store: TaskStore
    startup: Callable[[], Awaitable[None]]
    shutdown: Callable[[], Awaitable[None]]


async def _noop() -> None:
    return None


def build_task_store_runtime(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
) -> TaskStoreRuntime:
    if settings.a2a_task_store_backend == "memory":
        return TaskStoreRuntime(
            task_store=GuardedTaskStore(InMemoryTaskStore()),
            startup=_noop,
            shutdown=_noop,
        )

    resolved_engine = engine or build_database_engine(settings)
    raw_task_store = DatabaseTaskStore(engine=resolved_engine)
    task_store = GuardedTaskStore(raw_task_store)

    async def _startup() -> None:
        compat = DatabaseTaskStoreCompat(raw_task_store)
        await compat.validate_schema()
        await compat.initialize()

    async def _shutdown() -> None:
        if engine is None:
            await resolved_engine.dispose()

    return TaskStoreRuntime(
        task_store=task_store,
        startup=_startup,
        shutdown=_shutdown,
    )


def build_task_store(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
) -> TaskStore:
    return build_task_store_runtime(settings, engine=engine).task_store


def describe_lightweight_persistence_backend(settings: Settings) -> dict[str, str]:
    summary = {
        "backend": settings.a2a_task_store_backend,
        "scope": "sdk_tasks_and_adapter_state",
    }
    if settings.a2a_task_store_backend != "database":
        return summary
    url = make_url(cast(str, settings.a2a_task_store_database_url))
    summary["database_url"] = redact_database_url_for_logs(url.render_as_string(hide_password=True))
    summary["sqlite_tuning"] = (
        "local_durability_defaults" if url.drivername.startswith("sqlite") else "not_applicable"
    )
    return summary


def unwrap_task_store(task_store: TaskStore) -> TaskStore:
    inner = getattr(task_store, "_inner", None)
    if isinstance(inner, TaskStore):
        return unwrap_task_store(inner)
    return task_store


def _normalize_task_store_context(
    context: ServerCallContext | None,
) -> ServerCallContext:
    return normalize_server_call_context(context)


async def initialize_task_store(task_store: TaskStore) -> None:
    raw_task_store = unwrap_task_store(task_store)
    if isinstance(raw_task_store, DatabaseTaskStore):
        compat = DatabaseTaskStoreCompat(raw_task_store)
        await compat.validate_schema()
        await compat.initialize()
        return
    initialize = getattr(task_store, "initialize", None)
    if callable(initialize):
        await initialize()

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from a2a.server.context import ServerCallContext
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task, TaskState
from sqlalchemy import event, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url

from ..a2a_utils import proto_equals, proto_to_dict
from ..config import Settings
from ..task_states import TERMINAL_TASK_STATES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATE_VALUES = tuple(TaskState.Name(int(state)) for state in TERMINAL_TASK_STATES)
_ATOMIC_TERMINAL_GUARD_DIALECTS = frozenset({"postgresql", "sqlite"})
_SQLITE_JOURNAL_MODE = "WAL"
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_SYNCHRONOUS_MODE = "NORMAL"
_LIST_TASKS_BATCH_SIZE = 100


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
        dialect_name = task_store.engine.dialect.name
        if dialect_name not in _ATOMIC_TERMINAL_GUARD_DIALECTS:
            if not self._atomic_guard_fallback_logged:
                logger.warning(
                    "Database-backed task store dialect does not support atomic terminal guard; "
                    "falling back to read-before-write policy dialect=%s",
                    dialect_name,
                )
                self._atomic_guard_fallback_logged = True
            await self._save_with_read_before_write(task, context)
            return

        try:
            if await self._persist_with_atomic_terminal_guard(task_store, task, context):
                return
            existing = await self._load_task_from_database(task_store, task.id)
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

    async def _persist_with_atomic_terminal_guard(
        self,
        task_store: DatabaseTaskStore,
        task: Task,
        context: ServerCallContext,
    ) -> bool:
        await task_store._ensure_initialized()
        statement = _build_atomic_task_save_statement(
            task=task,
            owner=task_store.owner_resolver(context),
            task_table=task_store.task_model.__table__,
            dialect_name=task_store.engine.dialect.name,
        )
        async with task_store.async_session_maker.begin() as session:
            result = await session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def _load_task_from_database(
        self,
        task_store: DatabaseTaskStore,
        task_id: str,
    ) -> Task | None:
        await task_store._ensure_initialized()
        async with task_store.async_session_maker() as session:
            stmt = select(task_store.task_model).where(task_store.task_model.id == task_id)
            result = await session.execute(stmt)
            task_model = result.scalar_one_or_none()
        if task_model is None:
            return None
        return task_store._from_orm(task_model)

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


def build_task_store(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
) -> TaskStore:
    if settings.a2a_task_store_backend == "memory":
        return GuardedTaskStore(InMemoryTaskStore())

    resolved_engine = engine or build_database_engine(settings)
    return GuardedTaskStore(
        DatabaseTaskStore(
            engine=resolved_engine,
        )
    )


def describe_lightweight_persistence_backend(settings: Settings) -> dict[str, str]:
    summary = {
        "backend": settings.a2a_task_store_backend,
        "scope": "sdk_tasks_and_adapter_state",
    }
    if settings.a2a_task_store_backend != "database":
        return summary
    url = make_url(cast(str, settings.a2a_task_store_database_url))
    summary["database_url"] = url.render_as_string(hide_password=True)
    summary["sqlite_tuning"] = (
        "local_durability_defaults" if url.drivername.startswith("sqlite") else "not_applicable"
    )
    return summary


def build_database_engine(settings: Settings) -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    database_url = cast(str, settings.a2a_task_store_database_url)
    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        database_path = url.database
        if database_path and database_path != ":memory:" and not database_path.startswith("file:"):
            path = Path(database_path)
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        database_url,
        pool_pre_ping=not url.drivername.startswith("sqlite"),
    )
    if url.drivername.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def unwrap_task_store(task_store: TaskStore) -> TaskStore:
    inner = getattr(task_store, "_inner", None)
    if isinstance(inner, TaskStore):
        return unwrap_task_store(inner)
    return task_store


async def list_stored_tasks(
    task_store: TaskStore,
    context: ServerCallContext | None = None,
) -> list[Task]:
    normalized_context = _normalize_task_store_context(context)
    tasks: list[Task] = []
    next_page_token = ""

    while True:
        response = await task_store.list(
            ListTasksRequest(
                page_size=_LIST_TASKS_BATCH_SIZE,
                page_token=next_page_token,
            ),
            normalized_context,
        )
        tasks.extend(response.tasks)
        if not response.next_page_token:
            return tasks
        next_page_token = response.next_page_token


def _normalize_task_store_context(
    context: ServerCallContext | None,
) -> ServerCallContext:
    if context is not None:
        return context
    return ServerCallContext()


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA journal_mode={_SQLITE_JOURNAL_MODE}")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute(f"PRAGMA synchronous={_SQLITE_SYNCHRONOUS_MODE}")
    finally:
        cursor.close()


def _build_atomic_task_save_statement(
    *,
    task: Task,
    owner: str | None,
    task_table: Any,
    dialect_name: str,
):
    insert = _resolve_atomic_insert_factory(dialect_name)
    values = _task_row_values(task, owner=owner)
    status_state = task_table.c.status["state"].as_string()
    persist_guard = or_(
        task_table.c.status.is_(None),
        status_state.is_(None),
        status_state.not_in(_TERMINAL_TASK_STATE_VALUES),
    )
    return (
        insert(task_table)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[task_table.c.id],
            set_={key: value for key, value in values.items() if key != "id"},
            where=persist_guard,
        )
        .returning(task_table.c.id)
    )


def _resolve_atomic_insert_factory(dialect_name: str):
    if dialect_name == "sqlite":
        return sqlite_insert
    if dialect_name == "postgresql":
        return postgresql_insert
    raise ValueError(f"Unsupported atomic task persistence dialect: {dialect_name}")


def _task_row_values(task: Task, *, owner: str | None) -> dict[str, Any]:
    return {
        "id": task.id,
        "context_id": task.context_id,
        "kind": "task",
        "owner": owner,
        "last_updated": (
            task.status.timestamp.ToDatetime() if task.status.HasField("timestamp") else None
        ),
        "status": proto_to_dict(task.status),
        "artifacts": [proto_to_dict(artifact) for artifact in task.artifacts],
        "history": [proto_to_dict(message) for message in task.history],
        "metadata": proto_to_dict(task.metadata),
        "protocol_version": "1.0",
    }


async def initialize_task_store(task_store: TaskStore) -> None:
    initialize = getattr(task_store, "initialize", None)
    if callable(initialize):
        await initialize()

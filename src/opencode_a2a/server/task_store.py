from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import Task, TaskState

from ..config import Settings

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.completed,
        TaskState.canceled,
        TaskState.failed,
        TaskState.rejected,
    }
)


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
        if existing is None or existing.status.state not in _TERMINAL_TASK_STATES:
            return TaskPersistenceDecision(persist=True)
        if incoming.status.state != existing.status.state:
            return TaskPersistenceDecision(
                persist=False,
                reason="state_overwrite_after_terminal_persistence",
            )
        if incoming.model_dump(mode="json") != existing.model_dump(mode="json"):
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
        await self._inner.save(task, context)

    async def get(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        return await self._inner.get(task_id, context)

    async def delete(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> None:
        await self._inner.delete(task_id, context)


class TaskStoreOperationWrappingDecorator(TaskStoreDecorator):
    async def save(
        self,
        task: Task,
        context: ServerCallContext | None = None,
    ) -> None:
        try:
            await self._inner.save(task, context)
        except Exception as exc:
            raise TaskStoreOperationError("save", task.id) from exc

    async def get(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        try:
            return await self._inner.get(task_id, context)
        except Exception as exc:
            raise TaskStoreOperationError("get", task_id) from exc

    async def delete(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
    ) -> None:
        try:
            await self._inner.delete(task_id, context)
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

    async def save(
        self,
        task: Task,
        context: ServerCallContext | None = None,
    ) -> None:
        existing = await self._inner.get(task.id, context)
        decision = self._write_policy.evaluate(existing=existing, incoming=task)
        if not decision.persist:
            logger.warning(
                "Skipping task persistence by policy task_id=%s reason=%s existing_state=%s "
                "incoming_state=%s",
                task.id,
                decision.reason or "unspecified",
                None if existing is None else existing.status.state,
                task.status.state,
            )
            return
        await self._inner.save(task, context)


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
    from a2a.server.tasks.database_task_store import DatabaseTaskStore

    if settings.a2a_task_store_backend == "memory":
        return GuardedTaskStore(InMemoryTaskStore())

    resolved_engine = engine or build_database_engine(settings)
    return GuardedTaskStore(
        DatabaseTaskStore(
            engine=resolved_engine,
        )
    )


def build_database_engine(settings: Settings) -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    database_url = cast(str, settings.a2a_task_store_database_url)
    return create_async_engine(database_url)


async def initialize_task_store(task_store: TaskStore) -> None:
    initialize = getattr(task_store, "initialize", None)
    if callable(initialize):
        await initialize()

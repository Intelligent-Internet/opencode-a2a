from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from a2a.server.cluster import (
    ConcurrentTaskModificationError,
    DatabaseTaskEventStream,
    LegacyTaskStoreAdapter,
    StoredTask,
    TaskEventStream,
    TaskVersion,
    VersionedDatabaseTaskStore,
    VersionedTaskStore,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.tasks.copying_task_store import CopyingTaskStoreAdapter
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types import ListTasksRequest, ListTasksResponse, Task
from a2a.utils.errors import A2AError
from sqlalchemy import inspect
from sqlalchemy.engine import make_url

from ..config import Settings
from ..output_modes import (
    annotate_output_negotiation_metadata,
    extract_accepted_output_modes_from_metadata,
    normalize_accepted_output_modes,
)
from .context_helpers import normalize_server_call_context
from .database import build_database_engine, redact_database_url_for_logs

_SDK_TASK_COLUMNS = frozenset({"owner", "last_updated", "protocol_version"})
_SDK_TASK_INDEX = "idx_tasks_owner_last_updated"


class TaskStoreOperationError(RuntimeError):
    def __init__(self, operation: str, task_id: str | None) -> None:
        self.operation = operation
        self.task_id = task_id
        target = task_id or "unknown"
        super().__init__(f"Task store {operation} failed for task_id={target}")


class TaskOutputModeRegistry:
    """Request-scoped output modes, persisted into Task metadata by the store wrapper."""

    def __init__(self) -> None:
        self._modes: dict[str, tuple[str, ...]] = {}

    def remember(self, task_id: str, accepted_output_modes: Any) -> None:
        normalized = normalize_accepted_output_modes(accepted_output_modes)
        if normalized is None:
            self._modes.pop(task_id, None)
            return
        self._modes[task_id] = normalized

    def resolve(self, task_id: str, task: Task | None = None) -> tuple[str, ...] | None:
        if task_id in self._modes:
            return self._modes[task_id]
        if task is None:
            return None
        return extract_accepted_output_modes_from_metadata(task.metadata)

    def clear(self, task_id: str) -> None:
        self._modes.pop(task_id, None)

    def adopt_from_metadata(self, task_id: str, task: Task) -> None:
        if task_id in self._modes:
            return
        modes = extract_accepted_output_modes_from_metadata(task.metadata)
        if modes is not None:
            self._modes[task_id] = modes


class NegotiatingVersionedTaskStore(VersionedTaskStore):
    """Public VersionedTaskStore wrapper that persists output negotiation metadata."""

    def __init__(
        self,
        inner: VersionedTaskStore,
        output_modes: TaskOutputModeRegistry,
    ) -> None:
        self._inner = inner
        self._output_modes = output_modes
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        initialize = getattr(self._inner, "initialize", None)
        if callable(initialize):
            await initialize()
        self._initialized = True

    async def save(
        self,
        task: Task,
        *,
        event: Event | None,
        prev: Task | None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        await self._ensure_initialized()
        normalized_context = _normalize_task_store_context(context)
        accepted = self._output_modes.resolve(task.id, task)
        negotiated_task = annotate_output_negotiation_metadata(task, accepted)
        negotiated_event = annotate_output_negotiation_metadata(event, accepted)
        try:
            return await self._inner.save(
                cast(Task, negotiated_task),
                event=cast(Event | None, negotiated_event),
                prev=prev,
                prev_version=prev_version,
                context=normalized_context,
            )
        except TaskStoreOperationError:
            raise
        except ConcurrentTaskModificationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("save", task.id) from exc

    async def get(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> StoredTask | None:
        await self._ensure_initialized()
        try:
            stored = await self._inner.get(task_id, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("get", task_id) from exc
        if stored is not None:
            self._output_modes.adopt_from_metadata(task_id, stored.task)
        return stored

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        await self._ensure_initialized()
        try:
            return await self._inner.list(params, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("list", None) from exc

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        await self._ensure_initialized()
        try:
            await self._inner.delete(task_id, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("delete", task_id) from exc
        self._output_modes.clear(task_id)


class NegotiatingTaskStore(TaskStore):
    """TaskStore wrapper for process-local stores that persists output modes."""

    def __init__(
        self,
        inner: TaskStore,
        output_modes: TaskOutputModeRegistry,
    ) -> None:
        self._inner = inner
        self._output_modes = output_modes

    async def save(self, task: Task, context: ServerCallContext) -> None:
        normalized_context = _normalize_task_store_context(context)
        accepted = self._output_modes.resolve(task.id, task)
        negotiated_task = annotate_output_negotiation_metadata(task, accepted)
        try:
            await self._inner.save(cast(Task, negotiated_task), normalized_context)
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("save", task.id) from exc

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        try:
            task = await self._inner.get(task_id, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("get", task_id) from exc
        if task is not None:
            self._output_modes.adopt_from_metadata(task_id, task)
        return task

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        try:
            return await self._inner.list(params, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("list", None) from exc

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        try:
            await self._inner.delete(task_id, _normalize_task_store_context(context))
        except TaskStoreOperationError:
            raise
        except A2AError:
            raise
        except Exception as exc:
            raise TaskStoreOperationError("delete", task_id) from exc
        self._output_modes.clear(task_id)


@dataclass(slots=True)
class TaskStoreRuntime:
    task_store: TaskStore | VersionedTaskStore
    event_stream: TaskEventStream | None
    output_modes: TaskOutputModeRegistry
    startup: Callable[[], Awaitable[None]]
    shutdown: Callable[[], Awaitable[None]]


async def _noop() -> None:
    return None


def as_versioned_task_store(
    task_store: TaskStore | VersionedTaskStore,
) -> VersionedTaskStore:
    if isinstance(task_store, VersionedTaskStore):
        return task_store
    return LegacyTaskStoreAdapter(task_store)


def build_task_store_runtime(
    settings: Settings,
    *,
    engine: Any | None = None,
) -> TaskStoreRuntime:
    output_modes = TaskOutputModeRegistry()
    if settings.a2a_task_store_backend == "memory":
        return TaskStoreRuntime(
            task_store=CopyingTaskStoreAdapter(InMemoryTaskStore()),
            event_stream=None,
            output_modes=output_modes,
            startup=_noop,
            shutdown=_noop,
        )

    resolved_engine = engine or build_database_engine(settings)
    task_store = VersionedDatabaseTaskStore(
        engine=resolved_engine,
        create_table=True,
        table_name="tasks",
    )
    raw_task_store = cast(DatabaseTaskStore, task_store.as_task_store)
    event_stream = DatabaseTaskEventStream(engine=resolved_engine)

    async def _startup() -> None:
        await _validate_database_shape(raw_task_store)
        await task_store.initialize()
        await event_stream.initialize()

    async def _shutdown() -> None:
        if engine is None:
            await resolved_engine.dispose()

    return TaskStoreRuntime(
        task_store=task_store,
        event_stream=event_stream,
        output_modes=output_modes,
        startup=_startup,
        shutdown=_shutdown,
    )


async def _validate_database_shape(task_store: DatabaseTaskStore) -> None:
    database_url = redact_database_url_for_logs(
        task_store.engine.url.render_as_string(hide_password=True)
    )
    async with task_store.engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: _validate_sdk_task_table(
                sync_conn,
                database_url=database_url,
            )
        )


def _validate_sdk_task_table(connection: Any, *, database_url: str) -> None:
    inspector = inspect(connection)
    if "tasks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("tasks")}
    missing_columns = sorted(_SDK_TASK_COLUMNS - columns)
    if missing_columns:
        raise RuntimeError(
            f"SDK task store schema is incomplete at {database_url}; missing columns: "
            f"{', '.join(missing_columns)}. Run the upgraded SDK migration before startup."
        )
    indexes = {index["name"] for index in inspector.get_indexes("tasks")}
    if _SDK_TASK_INDEX not in indexes:
        raise RuntimeError(
            f"SDK task store schema is incomplete at {database_url}; missing index: "
            f"{_SDK_TASK_INDEX}. Run the upgraded SDK migration before startup."
        )


def build_task_store(
    settings: Settings,
    *,
    engine: Any | None = None,
) -> TaskStore | VersionedTaskStore:
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
    summary["sqlite_tuning"] = "local_durability_defaults"
    return summary


def unwrap_task_store(task_store: TaskStore | VersionedTaskStore) -> TaskStore | VersionedTaskStore:
    if isinstance(task_store, VersionedDatabaseTaskStore):
        return task_store.as_task_store
    if isinstance(task_store, NegotiatingVersionedTaskStore):
        return unwrap_task_store(task_store._inner)
    inner = getattr(task_store, "_store", None)
    if isinstance(inner, TaskStore | VersionedTaskStore):
        return unwrap_task_store(inner)
    return task_store


async def initialize_task_store(task_store: TaskStore | VersionedTaskStore) -> None:
    initialize = getattr(task_store, "initialize", None)
    if callable(initialize):
        await initialize()


async def task_store_snapshot(
    task_store: TaskStore | VersionedTaskStore,
    task_id: str,
    context: ServerCallContext | None = None,
) -> StoredTask | None:
    return await as_versioned_task_store(task_store).get(
        task_id,
        _normalize_task_store_context(context),
    )


async def task_store_get(
    task_store: TaskStore | VersionedTaskStore,
    task_id: str,
    context: ServerCallContext | None = None,
) -> Task | None:
    stored = await task_store_snapshot(task_store, task_id, context)
    return stored.task if stored is not None else None


async def task_store_save(
    task_store: TaskStore | VersionedTaskStore,
    task: Task,
    context: ServerCallContext | None = None,
) -> TaskVersion:
    await initialize_task_store(task_store)
    normalized_context = _normalize_task_store_context(context)
    versioned_store = as_versioned_task_store(task_store)
    stored = await versioned_store.get(task.id, normalized_context)
    return await versioned_store.save(
        task,
        event=None,
        prev=stored.task if stored is not None else None,
        prev_version=stored.version if stored is not None else TaskVersion.MISSING,
        context=normalized_context,
    )


async def task_store_delete(
    task_store: TaskStore | VersionedTaskStore,
    task_id: str,
    context: ServerCallContext | None = None,
) -> None:
    await as_versioned_task_store(task_store).delete(
        task_id,
        _normalize_task_store_context(context),
    )


def _normalize_task_store_context(
    context: ServerCallContext | None,
) -> ServerCallContext:
    return normalize_server_call_context(context)

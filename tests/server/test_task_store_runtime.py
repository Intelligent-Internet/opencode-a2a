from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a2a.server.cluster import (
    DatabaseTaskEventStream,
    LegacyTaskStoreAdapter,
    VersionedDatabaseTaskStore,
)
from a2a.server.context import ServerCallContext
from a2a.server.tasks.copying_task_store import CopyingTaskStoreAdapter
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import Task, TaskState, TaskStatus
from sqlalchemy import text

from opencode_a2a.output_modes import extract_accepted_output_modes_from_metadata
from opencode_a2a.server.database import build_database_engine, redact_database_url_for_logs
from opencode_a2a.server.task_store import (
    NegotiatingTaskStore,
    TaskOutputModeRegistry,
    TaskStoreOperationError,
    build_task_store_runtime,
    describe_lightweight_persistence_backend,
    initialize_task_store,
    task_store_delete,
    task_store_get,
    task_store_save,
)
from tests.support.settings import make_settings


def _task(task_id: str, *, context_id: str = "ctx-1") -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


@pytest.mark.asyncio
async def test_memory_runtime_uses_sdk_copying_store_and_versioned_adapter() -> None:
    runtime = build_task_store_runtime(
        make_settings(test_bearer_token="test-token", a2a_task_store_backend="memory")
    )
    assert isinstance(runtime.task_store, CopyingTaskStoreAdapter)
    assert runtime.event_stream is None
    assert isinstance(LegacyTaskStoreAdapter(runtime.task_store), LegacyTaskStoreAdapter)

    await runtime.startup()
    await task_store_save(runtime.task_store, _task("task-1"))
    assert await task_store_get(runtime.task_store, "task-1") == _task("task-1")
    await task_store_delete(runtime.task_store, "task-1")
    assert await task_store_get(runtime.task_store, "task-1") is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_database_runtime_uses_native_versioned_store_and_event_stream(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}",
    )
    runtime = build_task_store_runtime(settings)
    assert isinstance(runtime.task_store, VersionedDatabaseTaskStore)
    assert isinstance(runtime.event_stream, DatabaseTaskEventStream)

    await runtime.startup()
    context = ServerCallContext(state={"identity": "automation"})
    await task_store_save(runtime.task_store, _task("task-db"), context)
    assert await task_store_get(runtime.task_store, "task-db", context) == _task("task-db")
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_database_runtime_initializes_lazily_without_lifespan(tmp_path: Path) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'lazy.db'}",
    )
    runtime = build_task_store_runtime(settings)
    await task_store_save(runtime.task_store, _task("task-lazy"))
    assert await task_store_get(runtime.task_store, "task-lazy") == _task("task-lazy")
    await runtime.shutdown()


def test_describe_persistence_backend() -> None:
    assert describe_lightweight_persistence_backend(
        make_settings(test_bearer_token="test-token", a2a_task_store_backend="memory")
    ) == {
        "backend": "memory",
        "scope": "sdk_tasks_and_adapter_state",
    }
    assert describe_lightweight_persistence_backend(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_database_url="sqlite+aiosqlite:///./tasks.db",
        )
    ) == {
        "backend": "database",
        "scope": "sdk_tasks_and_adapter_state",
        "database_url": "sqlite+aiosqlite:///./tasks.db",
        "sqlite_tuning": "local_durability_defaults",
    }


def test_redact_database_url_for_logs() -> None:
    assert (
        redact_database_url_for_logs("sqlite+aiosqlite:///tasks.db?token=secret&pool_size=5")
        == "sqlite+aiosqlite:/tasks.db?token=%2A%2A%2A&pool_size=5"
    )


@pytest.mark.asyncio
async def test_database_runtime_rejects_incomplete_sdk_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    engine = build_database_engine(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{database_path}",
        )
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY)"))
    runtime = build_task_store_runtime(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{database_path}",
        ),
        engine=engine,
    )
    with pytest.raises(RuntimeError, match="missing columns"):
        await runtime.startup()
    await engine.dispose()


@pytest.mark.asyncio
async def test_output_negotiation_metadata_is_persisted() -> None:
    registry = TaskOutputModeRegistry()
    store = NegotiatingTaskStore(CopyingTaskStoreAdapter(InMemoryTaskStore()), registry)
    task = _task("task-negotiated")
    registry.remember(task.id, ["text/plain"])

    await store.save(task, ServerCallContext())
    restored = await store.get(task.id, ServerCallContext())

    assert restored is not None
    assert extract_accepted_output_modes_from_metadata(restored.metadata) == ("text/plain",)

    task.status.state = TaskState.TASK_STATE_COMPLETED
    await store.save(task, ServerCallContext())
    assert registry.resolve(task.id) is None
    restored = await store.get(task.id, ServerCallContext())
    assert restored is not None
    assert extract_accepted_output_modes_from_metadata(restored.metadata) == ("text/plain",)


@pytest.mark.asyncio
async def test_task_store_wraps_backend_failures() -> None:
    inner = AsyncMock()
    inner.get.side_effect = RuntimeError("backend unavailable")
    store = NegotiatingTaskStore(inner, TaskOutputModeRegistry())

    with pytest.raises(TaskStoreOperationError, match="get"):
        await store.get("task-1", ServerCallContext())


@pytest.mark.asyncio
async def test_shared_engine_is_not_disposed(tmp_path: Path) -> None:
    engine = build_database_engine(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}",
        )
    )
    runtime = build_task_store_runtime(
        make_settings(test_bearer_token="test-token"),
        engine=engine,
    )
    await initialize_task_store(runtime.task_store)
    await runtime.shutdown()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    await engine.dispose()

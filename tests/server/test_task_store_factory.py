from __future__ import annotations

import logging
import warnings
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.types import Task, TaskState, TaskStatus
from sqlalchemy import inspect, text

from opencode_a2a.server.migrations import CURRENT_TASK_STORE_SCHEMA_VERSION
from opencode_a2a.server.task_store import (
    FirstTerminalStateWinsPolicy,
    GuardedTaskStore,
    PolicyAwareTaskStore,
    TaskPersistenceDecision,
    TaskStoreOperationError,
    TaskStoreOperationWrappingDecorator,
    TaskWritePolicy,
    build_database_engine,
    build_task_store,
    describe_lightweight_persistence_backend,
    initialize_task_store,
    unwrap_task_store,
)
from tests.support.helpers import make_settings


def _task(task_id: str, *, context_id: str = "ctx-1") -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


def _set_status(task: Task, state: TaskState) -> Task:
    task.status.CopyFrom(TaskStatus(state=state))
    return task


def _set_metadata(task: Task, metadata: dict) -> Task:
    task.ClearField("metadata")
    task.metadata.update(metadata)
    return task


async def _read_task_store_schema_version(engine) -> int | None:  # noqa: ANN001
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT version FROM a2a_schema_version WHERE name = 'task_store'")
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None


async def _read_table_index_names(engine, table_name: str) -> set[str]:  # noqa: ANN001
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sync_conn: {
                index["name"] for index in inspect(sync_conn).get_indexes(table_name)
            }
        )


def test_build_task_store_defaults_to_database_backend(tmp_path: Path) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'default-tasks.db'}",
    )
    store = build_task_store(settings)

    assert isinstance(store, GuardedTaskStore)
    assert hasattr(store, "engine")


def test_build_task_store_allows_explicit_memory_backend() -> None:
    from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

    store = build_task_store(
        make_settings(test_bearer_token="test-token", a2a_task_store_backend="memory")
    )

    assert isinstance(store, GuardedTaskStore)
    assert isinstance(store._inner, TaskStoreOperationWrappingDecorator)
    assert isinstance(store._inner._inner, InMemoryTaskStore)


def test_describe_lightweight_persistence_backend_marks_sqlite_first_scope() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url="sqlite+aiosqlite:///./opencode-a2a.db",
    )

    assert describe_lightweight_persistence_backend(settings) == {
        "backend": "database",
        "scope": "sdk_tasks_and_adapter_state",
        "database_url": "sqlite+aiosqlite:///./opencode-a2a.db",
        "sqlite_tuning": "local_durability_defaults",
    }


def test_describe_lightweight_persistence_backend_supports_memory_backend() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_backend="memory",
    )

    assert describe_lightweight_persistence_backend(settings) == {
        "backend": "memory",
        "scope": "sdk_tasks_and_adapter_state",
    }


@pytest.mark.asyncio
async def test_database_task_store_persists_tasks_across_rebuilds(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        writer = build_task_store(settings)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        reader = build_task_store(settings)

    await initialize_task_store(writer)
    await writer.save(_task("task-1"))
    await writer.engine.dispose()

    await initialize_task_store(reader)
    restored = await reader.get("task-1")

    assert restored is not None
    assert restored.id == "task-1"
    assert restored.context_id == "ctx-1"
    assert restored.status.state == TaskState.TASK_STATE_WORKING

    await reader.engine.dispose()


@pytest.mark.asyncio
async def test_database_task_store_can_build_multiple_instances_without_warnings(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'warnings.db'}",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        first = build_task_store(settings)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        second = build_task_store(settings)

    await first.engine.dispose()
    await second.engine.dispose()


@pytest.mark.asyncio
async def test_build_database_engine_configures_sqlite_pragmas_and_parent_dir(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "nested" / "runtime.db"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{database_path}",
    )
    engine = build_database_engine(settings)

    try:
        async with engine.connect() as conn:
            journal_mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
            busy_timeout = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
            synchronous = (await conn.exec_driver_sql("PRAGMA synchronous")).scalar_one()
    finally:
        await engine.dispose()

    assert database_path.parent.exists()
    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 30_000
    assert int(synchronous) == 1


@pytest.mark.asyncio
async def test_database_task_store_upgrades_legacy_tasks_table_schema(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'legacy-tasks.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE tasks (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    context_id VARCHAR(36) NOT NULL,
                    kind VARCHAR(16) NOT NULL,
                    status JSON NOT NULL,
                    artifacts JSON,
                    history JSON,
                    metadata JSON
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id,
                    context_id,
                    kind,
                    status,
                    artifacts,
                    history,
                    metadata
                ) VALUES (
                    'legacy-task',
                    'ctx-1',
                    'task',
                    '{"state":"TASK_STATE_WORKING"}',
                    '[]',
                    '[]',
                    '{}'
                )
                """
            )
        )

    store = build_task_store(settings, engine=engine)
    await initialize_task_store(store)
    restored = await store.get("legacy-task")

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT owner, protocol_version
                FROM tasks
                WHERE id = 'legacy-task'
                """
            )
        )
        migrated_row = result.mappings().one()

    assert restored is not None
    assert restored.id == "legacy-task"
    assert restored.context_id == "ctx-1"
    assert restored.status.state == TaskState.TASK_STATE_WORKING
    assert migrated_row["owner"] == ""
    assert migrated_row["protocol_version"] == "1.0"
    assert await _read_task_store_schema_version(engine) == CURRENT_TASK_STORE_SCHEMA_VERSION
    assert "idx_tasks_owner_last_updated" in await _read_table_index_names(engine, "tasks")

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_task_store_does_not_dispose_shared_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'shared-engine.db'}",
    )
    engine = build_database_engine(settings)
    dispose_spy = AsyncMock()
    monkeypatch.setattr(type(engine), "dispose", dispose_spy)

    store = build_task_store(settings, engine=engine)
    await initialize_task_store(store)

    dispose_spy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "database"])
async def test_task_store_preserves_first_terminal_state(
    tmp_path: Path,
    backend: str,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_backend=backend,
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / f'{backend}.db'}",
    )
    store = build_task_store(settings)
    await initialize_task_store(store)

    completed = _set_status(_task("task-terminal"), TaskState.TASK_STATE_COMPLETED)
    await store.save(completed)

    late_failed = _set_status(_task("task-terminal"), TaskState.TASK_STATE_FAILED)
    await store.save(late_failed)

    restored = await store.get("task-terminal")
    assert restored is not None
    assert restored.status.state == TaskState.TASK_STATE_COMPLETED

    engine = getattr(store, "engine", None)
    if engine is not None:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_task_store_keeps_first_terminal_state_across_independent_instances(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'terminal-guard.db'}",
    )
    first = build_task_store(settings)
    second = build_task_store(settings)
    await initialize_task_store(first)
    await initialize_task_store(second)

    try:
        working = _task("task-1")
        await first.save(working)

        completed = _set_status(_task("task-1"), TaskState.TASK_STATE_COMPLETED)
        await first.save(completed)

        late_failed = _set_status(_task("task-1"), TaskState.TASK_STATE_FAILED)
        await second.save(late_failed)

        restored = await first.get("task-1")
    finally:
        await first.engine.dispose()
        await second.engine.dispose()

    assert restored is not None
    assert restored.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "database"])
async def test_task_store_rejects_late_mutation_after_terminal_state(
    tmp_path: Path,
    backend: str,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_backend=backend,
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / f'{backend}-late.db'}",
    )
    store = build_task_store(settings)
    await initialize_task_store(store)

    terminal = _set_status(_task("task-late"), TaskState.TASK_STATE_COMPLETED)
    await store.save(terminal)

    late_same_state = _set_status(_task("task-late"), TaskState.TASK_STATE_COMPLETED)
    _set_metadata(late_same_state, {"opencode": {"note": "late"}})
    await store.save(late_same_state)

    restored = await store.get("task-late")
    assert restored is not None
    assert restored.status.state == TaskState.TASK_STATE_COMPLETED
    assert not restored.metadata

    engine = getattr(store, "engine", None)
    if engine is not None:
        await engine.dispose()


@pytest.mark.asyncio
async def test_database_task_store_atomic_guard_does_not_depend_on_stale_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'stale-read.db'}",
    )
    first = build_task_store(settings)
    second = build_task_store(settings)
    await initialize_task_store(first)
    await initialize_task_store(second)

    try:
        working = _task("task-1")
        await first.save(working)

        completed = _set_status(_task("task-1"), TaskState.TASK_STATE_COMPLETED)
        await first.save(completed)

        late_completed = _set_status(_task("task-1"), TaskState.TASK_STATE_COMPLETED)
        _set_metadata(late_completed, {"opencode": {"late_mutation": True}})

        raw_second = unwrap_task_store(second)
        assert isinstance(raw_second, DatabaseTaskStore)
        original_get = DatabaseTaskStore.get.__get__(raw_second, DatabaseTaskStore)

        async def _stale_get(task_id: str, context=None) -> Task | None:  # noqa: ANN001
            del context
            if task_id == "task-1":
                return working
            return None

        monkeypatch.setattr(raw_second, "get", _stale_get)
        await second.save(late_completed)
        monkeypatch.setattr(raw_second, "get", original_get)

        restored = await first.get("task-1")
    finally:
        await first.engine.dispose()
        await second.engine.dispose()

    assert restored is not None
    assert restored.status.state == TaskState.TASK_STATE_COMPLETED
    assert not restored.metadata


@pytest.mark.asyncio
async def test_task_store_wraps_backend_failures() -> None:
    class _BrokenGetStore:
        async def get(self, task_id, context=None):  # noqa: ANN001
            del task_id, context
            raise RuntimeError("boom")

    class _BrokenSaveStore:
        async def get(self, task_id, context=None):  # noqa: ANN001
            del task_id, context
            return None

        async def save(self, task, context=None):  # noqa: ANN001
            del task, context
            raise RuntimeError("boom")

    class _BrokenDeleteStore:
        async def get(self, task_id, context=None):  # noqa: ANN001
            del task_id, context
            return None

        async def save(self, task, context=None):  # noqa: ANN001
            del task, context
            return None

        async def delete(self, task_id, context=None):  # noqa: ANN001
            del task_id, context
            raise RuntimeError("boom")

    store = TaskStoreOperationWrappingDecorator(_BrokenGetStore())

    with pytest.raises(TaskStoreOperationError, match="Task store get failed"):
        await store.get("task-1")

    store = TaskStoreOperationWrappingDecorator(_BrokenSaveStore())
    with pytest.raises(TaskStoreOperationError, match="Task store save failed"):
        await store.save(_task("task-1"))

    store = TaskStoreOperationWrappingDecorator(_BrokenDeleteStore())
    with pytest.raises(TaskStoreOperationError, match="Task store delete failed"):
        await store.delete("task-1")


def test_first_terminal_state_wins_policy_returns_explicit_decisions() -> None:
    policy = FirstTerminalStateWinsPolicy()

    completed = _set_status(_task("task-1"), TaskState.TASK_STATE_COMPLETED)

    assert policy.evaluate(existing=None, incoming=completed) == TaskPersistenceDecision(
        persist=True
    )

    failed = _set_status(_task("task-1"), TaskState.TASK_STATE_FAILED)
    assert policy.evaluate(existing=completed, incoming=failed) == TaskPersistenceDecision(
        persist=False,
        reason="state_overwrite_after_terminal_persistence",
    )

    late_completed = _set_status(_task("task-1"), TaskState.TASK_STATE_COMPLETED)
    _set_metadata(late_completed, {"opencode": {"note": "late"}})
    assert policy.evaluate(existing=completed, incoming=late_completed) == TaskPersistenceDecision(
        persist=False,
        reason="late_mutation_after_terminal_persistence",
    )


@pytest.mark.asyncio
async def test_policy_aware_task_store_uses_custom_write_policy() -> None:
    class _DenyAllPolicy(TaskWritePolicy):
        def evaluate(self, *, existing, incoming) -> TaskPersistenceDecision:  # noqa: ANN001
            del existing, incoming
            return TaskPersistenceDecision(persist=False, reason="deny_all")

    class _RecordingStore:
        def __init__(self) -> None:
            self.saved: list[Task] = []

        async def get(self, task_id, context=None):  # noqa: ANN001
            del task_id, context
            return None

        async def save(self, task, context=None):  # noqa: ANN001
            del context
            self.saved.append(task)

    inner = _RecordingStore()
    store = PolicyAwareTaskStore(inner, write_policy=_DenyAllPolicy())
    await store.save(_task("task-1"))

    assert inner.saved == []


@pytest.mark.asyncio
async def test_policy_aware_task_store_logs_warning_for_late_terminal_write(caplog) -> None:
    class _RecordingStore:
        def __init__(self, existing: Task) -> None:
            self.existing = existing
            self.saved: list[Task] = []

        async def get(self, task_id, context=None):  # noqa: ANN001
            del task_id, context
            return self.existing

        async def save(self, task, context=None):  # noqa: ANN001
            del context
            self.saved.append(task)

    completed = _set_status(_task("task-1"), TaskState.TASK_STATE_COMPLETED)

    inner = _RecordingStore(existing=completed)
    store = PolicyAwareTaskStore(inner)

    with caplog.at_level(logging.WARNING, logger="opencode_a2a.server.task_store"):
        await store.save(completed)

    assert inner.saved == [completed]
    assert any(
        "Received task persistence after terminal state" in record.message
        and "reason=accepted_duplicate" in record.message
        for record in caplog.records
    )

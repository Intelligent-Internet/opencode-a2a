from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from a2a.types import Task, TaskState, TaskStatus

from opencode_a2a.server.task_store import (
    GuardedTaskStore,
    TaskStoreOperationError,
    build_task_store,
    initialize_task_store,
)
from tests.support.helpers import make_settings


def _task(task_id: str, *, context_id: str = "ctx-1") -> Task:
    return Task(
        id=task_id,
        contextId=context_id,
        status=TaskStatus(state=TaskState.working),
    )


def test_build_task_store_defaults_to_database_backend(tmp_path: Path) -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'default-tasks.db'}",
    )
    store = build_task_store(settings)

    assert isinstance(store, GuardedTaskStore)
    assert hasattr(store, "engine")


def test_build_task_store_allows_explicit_memory_backend() -> None:
    from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

    store = build_task_store(
        make_settings(a2a_bearer_token="test-token", a2a_task_store_backend="memory")
    )

    assert isinstance(store, GuardedTaskStore)
    assert isinstance(store._inner, InMemoryTaskStore)


@pytest.mark.asyncio
async def test_database_task_store_persists_tasks_across_rebuilds(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    settings = make_settings(
        a2a_bearer_token="test-token",
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
    assert restored.status.state == TaskState.working

    await reader.engine.dispose()


@pytest.mark.asyncio
async def test_database_task_store_can_build_multiple_instances_without_warnings(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
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
@pytest.mark.parametrize("backend", ["memory", "database"])
async def test_task_store_preserves_first_terminal_state(
    tmp_path: Path,
    backend: str,
) -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_backend=backend,
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / f'{backend}.db'}",
    )
    store = build_task_store(settings)
    await initialize_task_store(store)

    completed = _task("task-terminal")
    completed.status = TaskStatus(state=TaskState.completed)
    await store.save(completed)

    late_failed = _task("task-terminal")
    late_failed.status = TaskStatus(state=TaskState.failed)
    await store.save(late_failed)

    restored = await store.get("task-terminal")
    assert restored is not None
    assert restored.status.state == TaskState.completed

    engine = getattr(store, "engine", None)
    if engine is not None:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "database"])
async def test_task_store_rejects_late_mutation_after_terminal_state(
    tmp_path: Path,
    backend: str,
) -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_backend=backend,
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / f'{backend}-late.db'}",
    )
    store = build_task_store(settings)
    await initialize_task_store(store)

    terminal = _task("task-late")
    terminal.status = TaskStatus(state=TaskState.completed)
    await store.save(terminal)

    late_same_state = _task("task-late")
    late_same_state.status = TaskStatus(state=TaskState.completed)
    late_same_state.metadata = {"opencode": {"note": "late"}}
    await store.save(late_same_state)

    restored = await store.get("task-late")
    assert restored is not None
    assert restored.status.state == TaskState.completed
    assert restored.metadata is None

    engine = getattr(store, "engine", None)
    if engine is not None:
        await engine.dispose()


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

    store = GuardedTaskStore(_BrokenGetStore())

    with pytest.raises(TaskStoreOperationError, match="Task store get failed"):
        await store.get("task-1")

    store = GuardedTaskStore(_BrokenSaveStore())
    with pytest.raises(TaskStoreOperationError, match="Task store save failed"):
        await store.save(_task("task-1"))

    store = GuardedTaskStore(_BrokenDeleteStore())
    with pytest.raises(TaskStoreOperationError, match="Task store delete failed"):
        await store.delete("task-1")

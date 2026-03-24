from __future__ import annotations

from pathlib import Path

import pytest
from a2a.types import Task, TaskState, TaskStatus

from opencode_a2a.server.task_store import (
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

    assert hasattr(store, "engine")


def test_build_task_store_allows_explicit_memory_backend() -> None:
    from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore

    store = build_task_store(
        make_settings(a2a_bearer_token="test-token", a2a_task_store_backend="memory")
    )

    assert isinstance(store, InMemoryTaskStore)


@pytest.mark.asyncio
async def test_database_task_store_persists_tasks_across_rebuilds(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
        a2a_task_store_table_name="tasks_test",
    )

    writer = build_task_store(settings)
    await initialize_task_store(writer)
    await writer.save(_task("task-1"))
    await writer.engine.dispose()

    reader = build_task_store(settings)
    await initialize_task_store(reader)
    restored = await reader.get("task-1")

    assert restored is not None
    assert restored.id == "task-1"
    assert restored.context_id == "ctx-1"
    assert restored.status.state == TaskState.working

    await reader.engine.dispose()

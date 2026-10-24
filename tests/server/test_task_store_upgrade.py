from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from a2a.server.cluster import ConcurrentTaskModificationError, TaskVersion
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Task, TaskState, TaskStatus
from google.protobuf.timestamp_pb2 import Timestamp

from opencode_a2a.server.context_helpers import normalize_server_call_context
from opencode_a2a.server.task_store import (
    as_versioned_task_store,
    build_task_store_runtime,
    task_store_get,
    task_store_save,
)
from tests.support.settings import make_settings


def _legacy_database(path: Path, state: str) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY, context_id VARCHAR(36), "
            "kind VARCHAR(16), status JSON, artifacts JSON, history JSON, metadata JSON)"
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-task",
                "legacy-context",
                "task",
                json.dumps({"state": state, "timestamp": "2026-01-02T03:04:05Z"}),
                json.dumps([{"artifactId": "result", "parts": [{"kind": "text", "text": "kept"}]}]),
                json.dumps(
                    [
                        {
                            "kind": "message",
                            "messageId": "input",
                            "role": "user",
                            "parts": [{"kind": "text", "text": "hello"}],
                        }
                    ]
                ),
                json.dumps({"opencode": {"session_id": "preserved-session"}}),
            ),
        )


def _upstream_upgrade(path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from a2a.a2a_db_cli import run_migrations; run_migrations()",
            "upgrade",
            "head",
            "--database-url",
            f"sqlite+aiosqlite:///{path}",
            "--add_columns_owner_last_updated-default-owner",
            "automation",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _timed_task(task_id: str, timestamp: str) -> Task:
    status_timestamp = Timestamp()
    status_timestamp.FromJsonString(timestamp)
    return Task(
        id=task_id,
        context_id="ctx-pagination",
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            timestamp=status_timestamp,
        ),
    )


@pytest.mark.asyncio
async def test_upgrade_preserves_legacy_task_and_owner(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _legacy_database(path, "completed")
    _upstream_upgrade(path)
    settings = make_settings(a2a_task_store_database_url=f"sqlite+aiosqlite:///{path}")
    context = normalize_server_call_context(ServerCallContext(state={"identity": "automation"}))
    other_context = ServerCallContext(state={"identity": "another-user"})
    runtime = build_task_store_runtime(settings)
    try:
        await runtime.startup()
        task = await task_store_get(runtime.task_store, "legacy-task", context)
        assert task is not None
        assert task.artifacts[0].parts[0].text == "kept"
        assert task.history[0].parts[0].text == "hello"
        assert await task_store_get(runtime.task_store, "legacy-task", other_context) is None
        assert not (
            await as_versioned_task_store(runtime.task_store).list(
                ListTasksRequest(),
                other_context,
            )
        ).tasks
    finally:
        await runtime.shutdown()

    with closing(sqlite3.connect(path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"task_events", "task_versions"} <= tables


@pytest.mark.asyncio
async def test_versioned_store_rejects_stale_first_write(tmp_path: Path) -> None:
    runtime = build_task_store_runtime(
        make_settings(
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'cas.db'}"
        )
    )
    context = normalize_server_call_context(ServerCallContext(state={"identity": "automation"}))
    task = Task(
        id="task-cas",
        context_id="ctx-cas",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    try:
        await runtime.startup()
        await task_store_save(runtime.task_store, task, context)
        with pytest.raises(ConcurrentTaskModificationError):
            await as_versioned_task_store(runtime.task_store).save(
                task,
                event=None,
                prev=None,
                prev_version=TaskVersion.MISSING,
                context=context,
            )
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "database"])
async def test_list_tasks_keyset_token_survives_first_page_delete(
    tmp_path: Path,
    backend: str,
) -> None:
    runtime = build_task_store_runtime(
        make_settings(
            a2a_task_store_backend=backend,
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'pagination.db'}",
        )
    )
    context = normalize_server_call_context(ServerCallContext(state={"identity": "automation"}))
    try:
        await runtime.startup()
        for task in (
            _timed_task("task-newest", "2026-10-24T03:00:00Z"),
            _timed_task("task-middle", "2026-10-24T02:00:00Z"),
            _timed_task("task-oldest", "2026-10-24T01:00:00Z"),
        ):
            await task_store_save(runtime.task_store, task, context)

        first_page = await as_versioned_task_store(runtime.task_store).list(
            ListTasksRequest(page_size=1),
            context,
        )
        assert [task.id for task in first_page.tasks] == ["task-newest"]
        assert first_page.next_page_token

        await as_versioned_task_store(runtime.task_store).delete("task-newest", context)
        second_page = await as_versioned_task_store(runtime.task_store).list(
            ListTasksRequest(page_size=1, page_token=first_page.next_page_token),
            context,
        )
        assert [task.id for task in second_page.tasks] == ["task-middle"]
    finally:
        await runtime.shutdown()

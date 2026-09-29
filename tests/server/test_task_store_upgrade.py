from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Task, TaskState, TaskStatus

from opencode_a2a.server.task_store import build_task_store_runtime, unwrap_task_store
from opencode_a2a.server.task_store_sdk_compat import TaskStoreSchemaCompatibilityError
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


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "failed", "canceled", "rejected"])
@pytest.mark.parametrize("normalize_rows", [False, True])
async def test_upstream_migrated_terminal_task_cannot_be_overwritten(
    tmp_path: Path, state: str, normalize_rows: bool
) -> None:
    path = tmp_path / "legacy.db"
    _legacy_database(path, state)
    _upstream_upgrade(path)
    runtime = build_task_store_runtime(
        make_settings(a2a_task_store_database_url=f"sqlite+aiosqlite:///{path}")
    )
    context = ServerCallContext(state={"identity": "automation"})
    try:
        if normalize_rows:
            await runtime.startup()
        else:
            # Protect callers of the store factory even before lifespan normalization.
            await unwrap_task_store(runtime.task_store).initialize()
        before = await runtime.task_store.get("legacy-task", context)
        assert before is not None
        await runtime.task_store.save(
            Task(
                id=before.id,
                context_id=before.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            ),
            context,
        )
        assert await runtime.task_store.get(before.id, context) == before
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_upgrade_preserves_contents_owner_and_is_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _legacy_database(path, "completed")
    _upstream_upgrade(path)
    _upstream_upgrade(path)
    settings = make_settings(a2a_task_store_database_url=f"sqlite+aiosqlite:///{path}")
    context = ServerCallContext(state={"identity": "automation"})
    other_context = ServerCallContext(state={"identity": "another-user"})
    runtime = build_task_store_runtime(settings)
    try:
        before = await runtime.task_store.get("legacy-task", context)
        assert before is not None
        assert before.artifacts[0].parts[0].text == "kept"
        assert before.history[0].parts[0].text == "hello"
        await runtime.startup()
        assert await runtime.task_store.get("legacy-task", context) == before
        assert await runtime.task_store.get("legacy-task", other_context) is None
        assert not (await runtime.task_store.list(ListTasksRequest(), other_context)).tasks
    finally:
        await runtime.shutdown()

    with closing(sqlite3.connect(path)) as connection:
        after_first = connection.execute("SELECT * FROM tasks").fetchall()
        assert connection.execute("SELECT protocol_version FROM tasks").fetchone() == ("1.0",)

    restarted = build_task_store_runtime(settings)
    try:
        await restarted.startup()
        assert await restarted.task_store.get("legacy-task", context) == before
    finally:
        await restarted.shutdown()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT * FROM tasks").fetchall() == after_first


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_last_row", [False, True])
async def test_legacy_row_conversion_batches_and_rolls_back_as_one_transaction(
    tmp_path: Path, invalid_last_row: bool
) -> None:
    path = tmp_path / "legacy.db"
    _legacy_database(path, "working")
    with closing(sqlite3.connect(path)) as connection, connection:
        for index in range(101):
            connection.execute(
                "INSERT INTO tasks SELECT ?, context_id, kind, status, artifacts, history, "
                "metadata FROM tasks WHERE id = 'legacy-task'",
                (f"task-{index:03}",),
            )
        if invalid_last_row:
            connection.execute(
                "UPDATE tasks SET status = ? WHERE id = 'task-100'",
                (json.dumps({"state": "invalid-state"}),),
            )
    _upstream_upgrade(path)
    runtime = build_task_store_runtime(
        make_settings(a2a_task_store_database_url=f"sqlite+aiosqlite:///{path}")
    )
    try:
        if invalid_last_row:
            with pytest.raises(TaskStoreSchemaCompatibilityError, match="legacy SDK task payload"):
                await runtime.startup()
        else:
            await runtime.startup()
    finally:
        await runtime.shutdown()
    with closing(sqlite3.connect(path)) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE protocol_version = '1.0'"
        ).fetchone()[0]
        assert count == (0 if invalid_last_row else 102)


@pytest.mark.asyncio
async def test_migrated_task_supports_status_and_timestamp_filters(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _legacy_database(path, "completed")
    _upstream_upgrade(path)
    runtime = build_task_store_runtime(
        make_settings(a2a_task_store_database_url=f"sqlite+aiosqlite:///{path}")
    )
    context = ServerCallContext(state={"identity": "automation"})
    try:
        await runtime.startup()
        request = ListTasksRequest(status=TaskState.TASK_STATE_COMPLETED)
        request.status_timestamp_after.FromJsonString("2026-01-01T00:00:00Z")
        result = await runtime.task_store.list(request, context)
        assert [task.id for task in result.tasks] == ["legacy-task"]
    finally:
        await runtime.shutdown()

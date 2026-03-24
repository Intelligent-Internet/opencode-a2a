from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect as sqlalchemy_inspect

from opencode_a2a.server.state_store import (
    DatabaseSessionStateRepository,
    MemorySessionStateRepository,
    build_interrupt_request_repository,
    build_session_state_repository,
    initialize_state_repository,
)
from opencode_a2a.server.task_store import build_database_engine
from tests.support.helpers import make_settings


@pytest.mark.asyncio
async def test_database_session_state_repository_persists_bindings(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'state.db'}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    writer = build_session_state_repository(settings, engine=engine)
    await initialize_state_repository(writer)
    await writer.set_session(identity="user-1", context_id="ctx-1", session_id="ses-1")
    await writer.set_owner(session_id="ses-1", identity="user-1")
    await writer.set_pending_claim(session_id="ses-2", identity="user-2")
    await engine.dispose()

    engine = build_database_engine(settings)
    reader = build_session_state_repository(settings, engine=engine)
    await initialize_state_repository(reader)

    assert await reader.get_session(identity="user-1", context_id="ctx-1") == "ses-1"
    assert await reader.get_owner(session_id="ses-1") == "user-1"
    assert await reader.get_pending_claim(session_id="ses-2") == "user-2"

    await engine.dispose()


@pytest.mark.asyncio
async def test_memory_pending_session_claim_expires() -> None:
    now = 100.0

    def _now() -> float:
        return now

    repository = MemorySessionStateRepository(
        ttl_seconds=3600,
        maxsize=128,
        pending_claim_ttl_seconds=5.0,
        clock=_now,
    )

    await repository.set_pending_claim(session_id="ses-1", identity="user-1")
    assert await repository.get_pending_claim(session_id="ses-1") == "user-1"

    now = 106.0
    assert await repository.get_pending_claim(session_id="ses-1") is None


@pytest.mark.asyncio
async def test_database_pending_session_claim_expires(tmp_path: Path) -> None:
    now = 100.0

    def _now() -> float:
        return now

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'pending-claim.db'}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)
    repository = DatabaseSessionStateRepository(
        engine=engine,
        pending_claim_ttl_seconds=5.0,
        clock=_now,
    )
    await initialize_state_repository(repository)

    await repository.set_pending_claim(session_id="ses-1", identity="user-1")
    assert await repository.get_pending_claim(session_id="ses-1") == "user-1"

    now = 106.0
    assert await repository.get_pending_claim(session_id="ses-1") is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_session_binding_and_owner_do_not_expire_with_time(tmp_path: Path) -> None:
    now = 100.0

    def _now() -> float:
        return now

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'durable-state.db'}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)
    repository = DatabaseSessionStateRepository(
        engine=engine,
        pending_claim_ttl_seconds=5.0,
        clock=_now,
    )
    await initialize_state_repository(repository)

    await repository.set_session(identity="user-1", context_id="ctx-1", session_id="ses-1")
    await repository.set_owner(session_id="ses-1", identity="user-1")

    now = 10_000.0
    assert await repository.get_session(identity="user-1", context_id="ctx-1") == "ses-1"
    assert await repository.get_owner(session_id="ses-1") == "user-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_interrupt_request_repository_persists_active_binding(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'interrupt.db'}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    writer = build_interrupt_request_repository(settings, engine=engine)
    await initialize_state_repository(writer)
    await writer.remember(
        request_id="perm-1",
        session_id="ses-1",
        interrupt_type="permission",
        identity="user-1",
        task_id="task-1",
        context_id="ctx-1",
        ttl_seconds=30.0,
    )
    await engine.dispose()

    engine = build_database_engine(settings)
    reader = build_interrupt_request_repository(settings, engine=engine)
    await initialize_state_repository(reader)
    status, binding = await reader.resolve(request_id="perm-1")

    assert status == "active"
    assert binding is not None
    assert binding.session_id == "ses-1"
    assert binding.interrupt_type == "permission"
    assert binding.identity == "user-1"
    assert binding.task_id == "task-1"
    assert binding.context_id == "ctx-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_state_repositories_skip_auto_create_when_disabled(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'state-no-create.db'}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
        a2a_task_store_create_table=False,
    )
    engine = build_database_engine(settings)

    session_repo = build_session_state_repository(settings, engine=engine)
    interrupt_repo = build_interrupt_request_repository(settings, engine=engine)
    await initialize_state_repository(session_repo)
    await initialize_state_repository(interrupt_repo)

    async with engine.begin() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: set(sqlalchemy_inspect(sync_conn).get_table_names())
        )

    assert "a2a_session_bindings" not in table_names
    assert "a2a_session_owners" not in table_names
    assert "a2a_pending_session_claims" not in table_names
    assert "a2a_interrupt_requests" not in table_names

    await engine.dispose()

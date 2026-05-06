from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.exc import IntegrityError

import opencode_a2a.server.migrations as migrations_module
import opencode_a2a.server.state_store as state_store_module
from opencode_a2a.server.database import build_database_engine
from opencode_a2a.server.migrations import CURRENT_STATE_STORE_SCHEMA_VERSION
from opencode_a2a.server.state_store import (
    _INTERRUPT_REQUESTS,
    DatabaseSessionStateRepository,
    MemorySessionStateRepository,
    build_interrupt_request_repository,
    build_runtime_state_runtime,
    build_session_state_repository,
    initialize_state_repository,
)
from tests.support.settings import make_settings


async def _read_state_store_schema_version(engine) -> int | None:  # noqa: ANN001
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT version FROM a2a_schema_version WHERE name = 'state_store'")
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None


async def _read_state_store_schema_row_count(engine) -> int:  # noqa: ANN001
    async with engine.begin() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM a2a_schema_version WHERE name = 'state_store'")
        )
        return int(result.scalar_one())


async def _read_table_index_names(engine, table_name: str) -> set[str]:  # noqa: ANN001
    async with engine.begin() as conn:
        return await conn.run_sync(
            lambda sync_conn: {
                index["name"] for index in inspect(sync_conn).get_indexes(table_name)
            }
        )


async def _read_pending_claim_row(engine, session_id: str) -> dict[str, object] | None:  # noqa: ANN001
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT session_id, identity, updated_at, expires_at
                FROM a2a_pending_session_claims
                WHERE session_id = :session_id
                """
            ),
            {"session_id": session_id},
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)


def test_add_missing_nullable_column_supports_non_sqlite_dialects(monkeypatch) -> None:
    executed: list[str] = []

    class _FakeInspector:
        def get_columns(self, _table_name: str) -> list[dict[str, str]]:
            return []

    class _FakeConnection:
        def __init__(self) -> None:
            self.dialect = postgresql_dialect()

        def execute(self, clause) -> None:  # noqa: ANN001
            executed.append(str(clause))

    monkeypatch.setattr(migrations_module, "inspect", lambda _connection: _FakeInspector())

    migrations_module._add_missing_nullable_column(
        _FakeConnection(),
        table=_INTERRUPT_REQUESTS,
        column_name="credential_id",
    )

    assert executed == ["ALTER TABLE a2a_interrupt_requests ADD COLUMN credential_id VARCHAR"]


def test_write_schema_version_recovers_from_concurrent_first_insert_race() -> None:
    executed: list[str] = []

    class _FakeResult:
        def __init__(self, value: int | None) -> None:
            self._value = value

        def scalar_one_or_none(self) -> int | None:
            return self._value

    class _FakeConnection:
        def execute(self, clause):  # noqa: ANN001
            executed.append(clause.__visit_name__)
            if clause.__visit_name__ == "select":
                return _FakeResult(None)
            if clause.__visit_name__ == "insert":
                raise IntegrityError("insert", {}, Exception("duplicate key"))
            if clause.__visit_name__ == "update":
                return None
            raise AssertionError(f"Unexpected clause type: {clause.__visit_name__}")

    migrations_module._write_schema_version(
        _FakeConnection(),
        version_table=migrations_module._SCHEMA_VERSIONS,
        scope=migrations_module.STATE_STORE_SCHEMA_NAME,
        version=1,
    )

    assert executed == ["select", "insert", "update"]


@pytest.mark.asyncio
async def test_state_store_write_helper_recovers_from_concurrent_first_insert_race() -> None:
    executed: list[str] = []

    class _FakeSession:
        async def execute(self, clause):  # noqa: ANN001
            executed.append(clause.__visit_name__)
            if clause.__visit_name__ == "insert":
                raise IntegrityError("insert", {}, Exception("duplicate key"))
            if clause.__visit_name__ == "update":
                return None
            raise AssertionError(f"Unexpected clause type: {clause.__visit_name__}")

    await state_store_module._insert_then_update_on_conflict(
        _FakeSession(),
        table=_INTERRUPT_REQUESTS,
        key_values={"request_id": "perm-1"},
        update_values={
            "session_id": "ses-1",
            "interrupt_type": "permission",
            "identity": "user-1",
            "credential_id": "cred-1",
            "task_id": "task-1",
            "context_id": "ctx-1",
            "details_json": None,
            "expires_at": 1.0,
            "tombstone_expires_at": None,
        },
    )

    assert executed == ["insert", "update"]


@pytest.mark.asyncio
async def test_database_session_state_repository_persists_bindings(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'state.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
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
    assert await _read_state_store_schema_version(engine) == CURRENT_STATE_STORE_SCHEMA_VERSION

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
        test_bearer_token="test-token",
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
async def test_database_pending_session_claim_keeps_absolute_expiry_when_runtime_ttl_changes(
    tmp_path: Path,
) -> None:
    now = 100.0

    def _now() -> float:
        return now

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'pending-claim-expires-at.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)
    writer = DatabaseSessionStateRepository(
        engine=engine,
        pending_claim_ttl_seconds=5.0,
        clock=_now,
    )
    await initialize_state_repository(writer)
    await writer.set_pending_claim(session_id="ses-1", identity="user-1")

    stored_row = await _read_pending_claim_row(engine, "ses-1")
    assert stored_row is not None
    assert stored_row["updated_at"] is None
    assert stored_row["expires_at"] == pytest.approx(105.0)

    reader = DatabaseSessionStateRepository(
        engine=engine,
        pending_claim_ttl_seconds=1.0,
        clock=_now,
    )
    await initialize_state_repository(reader)

    now = 104.0
    assert await reader.get_pending_claim(session_id="ses-1") == "user-1"

    now = 106.0
    assert await reader.get_pending_claim(session_id="ses-1") is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_state_store_drops_legacy_pending_claim_rows_during_migration(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'legacy-pending-claim.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_session_bindings (
                    identity VARCHAR NOT NULL,
                    context_id VARCHAR NOT NULL,
                    session_id VARCHAR NOT NULL,
                    PRIMARY KEY (identity, context_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_session_owners (
                    session_id VARCHAR NOT NULL PRIMARY KEY,
                    identity VARCHAR NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_pending_session_claims (
                    session_id VARCHAR NOT NULL PRIMARY KEY,
                    identity VARCHAR NOT NULL,
                    updated_at FLOAT NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO a2a_pending_session_claims (
                    session_id,
                    identity,
                    updated_at
                ) VALUES (
                    'ses-legacy',
                    'user-legacy',
                    100.0
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_interrupt_requests (
                    request_id VARCHAR NOT NULL PRIMARY KEY,
                    session_id VARCHAR,
                    interrupt_type VARCHAR,
                    identity VARCHAR,
                    credential_id VARCHAR,
                    task_id VARCHAR,
                    context_id VARCHAR,
                    details_json VARCHAR,
                    expires_at FLOAT,
                    tombstone_expires_at FLOAT
                )
                """
            )
        )

    repository = build_session_state_repository(settings, engine=engine)
    await initialize_state_repository(repository)

    assert await repository.get_pending_claim(session_id="ses-legacy") is None
    assert await _read_pending_claim_row(engine, "ses-legacy") is None
    assert await _read_state_store_schema_version(engine) == CURRENT_STATE_STORE_SCHEMA_VERSION

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_session_binding_and_owner_do_not_expire_with_time(tmp_path: Path) -> None:
    now = 100.0

    def _now() -> float:
        return now

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'durable-state.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
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
        test_bearer_token="test-token",
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
        credential_id="cred-1",
        task_id="task-1",
        context_id="ctx-1",
        details={"permission": "read", "patterns": ["/tmp/config.yml"]},
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
    assert binding.credential_id == "cred-1"
    assert binding.task_id == "task-1"
    assert binding.context_id == "ctx-1"
    assert binding.details == {"permission": "read", "patterns": ["/tmp/config.yml"]}

    await engine.dispose()


@pytest.mark.asyncio
async def test_interrupt_request_repository_lists_pending_items_by_identity_and_type(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'interrupt-list.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)
    repository = build_interrupt_request_repository(settings, engine=engine)
    await initialize_state_repository(repository)

    await repository.remember(
        request_id="perm-1",
        session_id="ses-1",
        interrupt_type="permission",
        identity="user-1",
        credential_id="cred-1",
        task_id="task-1",
        context_id="ctx-1",
        details={"permission": "read"},
        ttl_seconds=60.0,
    )
    await repository.remember(
        request_id="q-1",
        session_id="ses-2",
        interrupt_type="question",
        identity="user-1",
        credential_id="cred-2",
        task_id="task-2",
        context_id="ctx-2",
        details={"questions": [{"question": "Proceed?"}]},
        ttl_seconds=60.0,
    )
    await repository.remember(
        request_id="perm-other",
        session_id="ses-3",
        interrupt_type="permission",
        identity="user-2",
        credential_id="cred-3",
        task_id="task-3",
        context_id="ctx-3",
        details={"permission": "write"},
        ttl_seconds=60.0,
    )

    permission_items = await repository.list_pending(
        identity="user-1",
        interrupt_type="permission",
    )
    question_items = await repository.list_pending(
        identity="user-1",
        interrupt_type="question",
    )

    assert [item.request_id for item in permission_items] == ["perm-1"]
    assert permission_items[0].credential_id == "cred-1"
    assert permission_items[0].details == {"permission": "read"}
    assert [item.request_id for item in question_items] == ["q-1"]
    assert question_items[0].credential_id == "cred-2"
    assert question_items[0].details == {"questions": [{"question": "Proceed?"}]}

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_interrupt_request_repository_upgrades_legacy_interrupt_table(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'legacy-interrupt.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_interrupt_requests (
                    request_id VARCHAR NOT NULL PRIMARY KEY,
                    session_id VARCHAR,
                    interrupt_type VARCHAR,
                    identity VARCHAR,
                    task_id VARCHAR,
                    context_id VARCHAR,
                    expires_at FLOAT,
                    tombstone_expires_at FLOAT
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO a2a_interrupt_requests (
                    request_id,
                    session_id,
                    interrupt_type,
                    identity,
                    task_id,
                    context_id,
                    expires_at,
                    tombstone_expires_at
                ) VALUES (
                    'perm-legacy',
                    'ses-legacy',
                    'permission',
                    'user-1',
                    'task-legacy',
                    'ctx-legacy',
                    4102444800.0,
                    NULL
                )
                """
            )
        )

    repository = build_interrupt_request_repository(settings, engine=engine)
    await initialize_state_repository(repository)

    status, binding = await repository.resolve(request_id="perm-legacy")
    pending = await repository.list_pending(identity="user-1", interrupt_type="permission")

    assert status == "active"
    assert binding is not None
    assert binding.session_id == "ses-legacy"
    assert binding.credential_id is None
    assert binding.details is None
    assert [item.request_id for item in pending] == ["perm-legacy"]
    assert pending[0].credential_id is None
    assert pending[0].details is None
    assert await _read_state_store_schema_version(engine) == CURRENT_STATE_STORE_SCHEMA_VERSION

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_state_store_records_schema_version_for_existing_current_schema(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'current-schema.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_session_bindings (
                    identity VARCHAR NOT NULL,
                    context_id VARCHAR NOT NULL,
                    session_id VARCHAR NOT NULL,
                    PRIMARY KEY (identity, context_id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_session_owners (
                    session_id VARCHAR NOT NULL PRIMARY KEY,
                    identity VARCHAR NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_pending_session_claims (
                    session_id VARCHAR NOT NULL PRIMARY KEY,
                    identity VARCHAR NOT NULL,
                    updated_at FLOAT NOT NULL
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE a2a_interrupt_requests (
                    request_id VARCHAR NOT NULL PRIMARY KEY,
                    session_id VARCHAR,
                    interrupt_type VARCHAR,
                    identity VARCHAR,
                    credential_id VARCHAR,
                    task_id VARCHAR,
                    context_id VARCHAR,
                    details_json VARCHAR,
                    expires_at FLOAT,
                    tombstone_expires_at FLOAT
                )
                """
            )
        )

    session_repository = build_session_state_repository(settings, engine=engine)
    await initialize_state_repository(session_repository)

    assert await _read_state_store_schema_version(engine) == CURRENT_STATE_STORE_SCHEMA_VERSION
    assert await _read_table_index_names(engine, "a2a_pending_session_claims") == {
        "ix_a2a_pending_session_claims_expires_at"
    }
    assert await _read_table_index_names(engine, "a2a_interrupt_requests") == {
        "ix_a2a_interrupt_requests_identity_expires_at",
        "ix_a2a_interrupt_requests_identity_type_expires_at",
        "ix_a2a_interrupt_requests_tombstone_expires_at",
    }

    await engine.dispose()


@pytest.mark.asyncio
async def test_database_state_store_initialization_is_idempotent_across_repositories(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'idempotent-state.db'}"
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )
    engine = build_database_engine(settings)

    session_repository = build_session_state_repository(settings, engine=engine)
    interrupt_repository = build_interrupt_request_repository(settings, engine=engine)

    await initialize_state_repository(session_repository)
    await initialize_state_repository(interrupt_repository)
    await initialize_state_repository(session_repository)

    assert await _read_state_store_schema_version(engine) == CURRENT_STATE_STORE_SCHEMA_VERSION
    assert await _read_state_store_schema_row_count(engine) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_build_runtime_state_runtime_initializes_and_preserves_shared_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime-state.db'}",
    )
    engine = build_database_engine(settings)
    dispose_spy = AsyncMock()
    monkeypatch.setattr(type(engine), "dispose", dispose_spy)

    runtime = build_runtime_state_runtime(settings, engine=engine)
    await runtime.startup()
    await runtime.shutdown()

    assert await _read_state_store_schema_version(engine) == CURRENT_STATE_STORE_SCHEMA_VERSION
    dispose_spy.assert_not_awaited()

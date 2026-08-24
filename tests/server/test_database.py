from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest

from opencode_a2a.server import database as database_module
from opencode_a2a.server.database import build_database_engine
from tests.support.settings import make_settings


def _settings_for(tmp_path: Path, name: str = "runtime.db"):
    return make_settings(
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / name}",
    )


@pytest.mark.asyncio
async def test_build_database_engine_configures_sqlite_pragmas(tmp_path) -> None:
    settings = make_settings(
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{(tmp_path / 'runtime.db').resolve()}",
    )
    engine = build_database_engine(settings)

    try:
        async with engine.connect() as conn:
            journal_mode = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
            busy_timeout = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar_one()
            synchronous = (await conn.exec_driver_sql("PRAGMA synchronous")).scalar_one()
    finally:
        await engine.dispose()

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 30_000
    assert int(synchronous) == 1


@pytest.mark.asyncio
async def test_build_database_engine_skips_hardening_for_memory_database() -> None:
    settings = make_settings(
        a2a_task_store_database_url="sqlite+aiosqlite:///:memory:",
    )
    engine = build_database_engine(settings)
    try:
        async with engine.connect() as conn:
            assert (await conn.exec_driver_sql("SELECT 1")).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_build_database_engine_skips_hardening_for_inline_memory_file_uri() -> None:
    settings = make_settings(
        a2a_task_store_database_url=(
            "sqlite+aiosqlite:///file:memdb_shared?mode=memory&cache=shared&uri=true"
        ),
    )
    engine = build_database_engine(settings)
    try:
        async with engine.connect() as conn:
            assert (await conn.exec_driver_sql("SELECT 1")).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_build_database_engine_creates_private_database_file_and_directory(tmp_path) -> None:
    database_dir = tmp_path / "private"
    database_path = database_dir / "runtime.db"
    settings = make_settings(
        a2a_task_store_database_url=f"sqlite+aiosqlite:///{database_path}",
    )

    build_database_engine(settings)

    assert database_path.is_file()
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(database_dir.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_build_database_engine_hardens_existing_database_file(tmp_path) -> None:
    database_path = tmp_path / "runtime.db"
    database_path.write_bytes(b"")
    database_path.chmod(0o644)

    build_database_engine(_settings_for(tmp_path))

    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_build_database_engine_rejects_symlink_database(tmp_path) -> None:
    target = tmp_path / "target.db"
    target.write_bytes(b"")
    link = tmp_path / "runtime.db"
    link.symlink_to(target)

    with pytest.raises(RuntimeError, match="symlink"):
        build_database_engine(_settings_for(tmp_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_build_database_engine_rejects_symlink_for_relative_database_url(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target.db"
    target.write_bytes(b"")
    Path("runtime.db").symlink_to(target)

    with pytest.raises(RuntimeError, match="symlink"):
        build_database_engine(
            make_settings(
                a2a_task_store_database_url="sqlite+aiosqlite:///runtime.db",
            )
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_build_database_engine_rejects_foreign_owner_database(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "runtime.db"
    database_path.write_bytes(b"")

    current_euid = os.geteuid()
    monkeypatch.setattr(database_module.os, "geteuid", lambda: current_euid + 100_000)

    with pytest.raises(RuntimeError, match="owned by uid"):
        build_database_engine(_settings_for(tmp_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_build_database_engine_rejects_directory_database_path(tmp_path) -> None:
    (tmp_path / "runtime.db").mkdir()

    with pytest.raises(RuntimeError, match="regular file"):
        build_database_engine(_settings_for(tmp_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
@pytest.mark.asyncio
async def test_build_database_engine_converges_wal_sidecar_modes(tmp_path) -> None:
    settings = _settings_for(tmp_path)
    engine = build_database_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("CREATE TABLE sample (value INTEGER)")
            await conn.exec_driver_sql("INSERT INTO sample VALUES (1)")
    finally:
        await engine.dispose()

    database_path = tmp_path / "runtime.db"
    for suffix in ("", "-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists():
            assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
@pytest.mark.asyncio
async def test_build_database_engine_rejects_symlink_wal_sidecar(tmp_path) -> None:
    settings = _settings_for(tmp_path)
    engine = build_database_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("CREATE TABLE sample (value INTEGER)")
            await conn.exec_driver_sql("INSERT INTO sample VALUES (1)")
    finally:
        await engine.dispose()

    target = tmp_path / "target-wal.db"
    target.write_bytes(b"")
    wal_sidecar = Path(f"{tmp_path / 'runtime.db'}-wal")
    if wal_sidecar.exists():
        wal_sidecar.unlink()
    wal_sidecar.symlink_to(target)

    with pytest.raises(RuntimeError, match="sidecar"):
        build_database_engine(_settings_for(tmp_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_apply_private_sqlite_modes_rejects_foreign_owned_sidecar(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "runtime.db"
    database_path.write_bytes(b"")
    sidecar = tmp_path / "runtime.db-wal"
    sidecar.write_bytes(b"")

    current_euid = os.geteuid()
    monkeypatch.setattr(database_module.os, "geteuid", lambda: current_euid + 100_000)

    with pytest.raises(RuntimeError, match="owned by uid"):
        database_module._apply_private_sqlite_modes(database_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
@pytest.mark.asyncio
async def test_build_database_engine_rechecks_hardening_on_new_connection(
    tmp_path,
    monkeypatch,
) -> None:
    created_connections: list[aiosqlite.Connection] = []
    original_connect = aiosqlite.connect

    def tracking_connect(*args: Any, **kwargs: Any) -> aiosqlite.Connection:
        connection = original_connect(*args, **kwargs)
        created_connections.append(connection)
        return connection

    monkeypatch.setattr(aiosqlite, "connect", tracking_connect)
    settings = _settings_for(tmp_path)
    engine = build_database_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("CREATE TABLE sample (value INTEGER)")
        await engine.dispose()

        victim = tmp_path / "victim.db"
        victim.write_bytes(b"")
        database_path = tmp_path / "runtime.db"
        database_path.unlink()
        database_path.symlink_to(victim)

        with pytest.raises(RuntimeError, match="symlink"):
            async with engine.connect() as conn:
                await conn.exec_driver_sql("SELECT 1")
    finally:
        await engine.dispose()

    assert len(created_connections) == 1


def test_configure_sqlite_connection_closes_connection_on_failure() -> None:
    dbapi_connection = MagicMock()
    cursor = dbapi_connection.cursor.return_value
    cursor.execute.side_effect = RuntimeError("PRAGMA failed")
    dbapi_connection.close.side_effect = OSError("close failed")

    with pytest.raises(RuntimeError, match="PRAGMA failed"):
        database_module._configure_sqlite_connection(dbapi_connection, MagicMock())

    cursor.close.assert_called_once_with()
    dbapi_connection.close.assert_called_once_with()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permission semantics")
def test_configure_sqlite_connection_closes_connection_on_hardening_failure(tmp_path) -> None:
    victim = tmp_path / "victim.db"
    victim.write_bytes(b"")
    database_path = tmp_path / "runtime.db"
    database_path.symlink_to(victim)
    dbapi_connection = MagicMock()

    with pytest.raises(RuntimeError, match="symlink"):
        database_module._configure_sqlite_connection(
            dbapi_connection,
            MagicMock(),
            database_path=database_path,
        )

    dbapi_connection.cursor.assert_not_called()
    dbapi_connection.close.assert_called_once_with()

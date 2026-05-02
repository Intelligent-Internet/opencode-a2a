from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..config import Settings

_SQLITE_JOURNAL_MODE = "WAL"
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_SYNCHRONOUS_MODE = "NORMAL"


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA journal_mode={_SQLITE_JOURNAL_MODE}")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute(f"PRAGMA synchronous={_SQLITE_SYNCHRONOUS_MODE}")
    finally:
        cursor.close()


def build_database_engine(settings: Settings) -> AsyncEngine:
    database_url = cast(str, settings.a2a_task_store_database_url)
    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        database_path = url.database
        if database_path and database_path != ":memory:" and not database_path.startswith("file:"):
            path = Path(database_path)
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        database_url,
        pool_pre_ping=not url.drivername.startswith("sqlite"),
    )
    if url.drivername.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine

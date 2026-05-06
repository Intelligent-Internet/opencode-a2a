from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..config import Settings

_SQLITE_JOURNAL_MODE = "WAL"
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_SYNCHRONOUS_MODE = "NORMAL"
_SENSITIVE_DATABASE_QUERY_KEYS = frozenset(
    {"password", "passwd", "pwd", "token", "secret", "api_key", "apikey", "access_token"}
)


def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA journal_mode={_SQLITE_JOURNAL_MODE}")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute(f"PRAGMA synchronous={_SQLITE_SYNCHRONOUS_MODE}")
    finally:
        cursor.close()


def redact_database_url_for_logs(database_url: str) -> str:
    parts = urlsplit(database_url)
    if not parts.query:
        return database_url

    redacted_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SENSITIVE_DATABASE_QUERY_KEYS:
            redacted_query.append((key, "***"))
            continue
        redacted_query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(redacted_query)))


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

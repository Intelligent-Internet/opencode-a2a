from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

STATE_STORE_SCHEMA_NAME = "state_store"
CURRENT_STATE_STORE_SCHEMA_VERSION = 5
TASK_STORE_SCHEMA_NAME = "task_store"
CURRENT_TASK_STORE_SCHEMA_VERSION = 3

_SCHEMA_VERSION_METADATA = MetaData()

_SCHEMA_VERSIONS = Table(
    "a2a_schema_version",
    _SCHEMA_VERSION_METADATA,
    Column("name", String, primary_key=True),
    Column("version", Integer, nullable=False),
)


def _add_missing_nullable_column(
    connection: Connection,
    *,
    table: Table,
    column_name: str,
) -> None:
    existing_columns = {column["name"] for column in inspect(connection).get_columns(table.name)}
    if column_name in existing_columns:
        return
    column = table.c[column_name]
    if column.primary_key or not column.nullable:
        raise RuntimeError(f"Unsupported state-store migration for {table.name}.{column_name}")
    preparer = connection.dialect.identifier_preparer
    table_name_sql = preparer.quote(table.name)
    column_name_sql = preparer.quote(column_name)
    column_type_sql = column.type.compile(dialect=connection.dialect)
    connection.execute(
        text(f"ALTER TABLE {table_name_sql} ADD COLUMN {column_name_sql} {column_type_sql}")
    )


def _migration_1_add_interrupt_details_json(
    connection: Connection,
    *,
    interrupt_requests_table: Table,
) -> None:
    _add_missing_nullable_column(
        connection,
        table=interrupt_requests_table,
        column_name="details_json",
    )


def _migration_2_add_pending_claim_expires_at(
    connection: Connection,
    *,
    pending_session_claims_table: Table,
) -> None:
    _add_missing_nullable_column(
        connection,
        table=pending_session_claims_table,
        column_name="expires_at",
    )


def _create_missing_index(
    connection: Connection,
    *,
    index: Index,
) -> None:
    table = index.table
    if table is None:
        raise RuntimeError("State-store index is missing table metadata")
    existing_indexes = {
        existing_index["name"] for existing_index in inspect(connection).get_indexes(table.name)
    }
    if index.name in existing_indexes:
        return
    index.create(connection)


def _migration_3_add_lightweight_state_indexes(
    connection: Connection,
    *,
    pending_session_claims_table: Table,
    interrupt_requests_table: Table,
) -> None:
    indexes = sorted(
        [
            *pending_session_claims_table.indexes,
            *interrupt_requests_table.indexes,
        ],
        key=lambda index: index.name or "",
    )
    for index in indexes:
        _create_missing_index(connection, index=index)


def _migration_4_add_interrupt_credential_id(
    connection: Connection,
    *,
    interrupt_requests_table: Table,
) -> None:
    _add_missing_nullable_column(
        connection,
        table=interrupt_requests_table,
        column_name="credential_id",
    )


def _migration_5_drop_legacy_pending_claim_rows(
    connection: Connection,
    *,
    pending_session_claims_table: Table,
) -> None:
    connection.execute(
        pending_session_claims_table.delete().where(
            pending_session_claims_table.c.expires_at.is_(None)
        )
    )


def _migration_1_add_task_store_sdk_columns(
    connection: Connection,
    *,
    task_table: Table,
) -> None:
    for column_name in ("owner", "last_updated", "protocol_version"):
        _add_missing_nullable_column(
            connection,
            table=task_table,
            column_name=column_name,
        )


def _migration_2_backfill_task_store_sdk_defaults(
    connection: Connection,
    *,
    task_table: Table,
) -> None:
    connection.execute(task_table.update().where(task_table.c.owner.is_(None)).values(owner=""))
    connection.execute(
        task_table.update()
        .where(task_table.c.protocol_version.is_(None))
        .values(protocol_version="1.0")
    )


def _migration_3_add_task_store_indexes(
    connection: Connection,
    *,
    task_table: Table,
) -> None:
    for index in sorted(task_table.indexes, key=lambda item: item.name or ""):
        _create_missing_index(connection, index=index)


def _read_schema_version(
    connection: Connection,
    *,
    version_table: Table,
    scope: str,
) -> int | None:
    result = connection.execute(
        select(version_table.c.version).where(version_table.c.name == scope)
    )
    version = result.scalar_one_or_none()
    return int(version) if version is not None else None


def _write_schema_version(
    connection: Connection,
    *,
    version_table: Table,
    scope: str,
    version: int,
) -> None:
    existing_version = _read_schema_version(
        connection,
        version_table=version_table,
        scope=scope,
    )
    if existing_version is not None:
        connection.execute(
            update(version_table).where(version_table.c.name == scope).values(version=version)
        )
        return
    try:
        connection.execute(insert(version_table).values(name=scope, version=version))
    except IntegrityError:
        connection.execute(
            update(version_table).where(version_table.c.name == scope).values(version=version)
        )


def _apply_schema_migrations(
    connection: Connection,
    *,
    version_table: Table,
    scope: str,
    current_version: int,
    migrations: Mapping[int, Callable[[Connection], None]],
) -> int:
    if current_version < 0:
        raise ValueError("current_version must be non-negative")

    stored_version = _read_schema_version(
        connection,
        version_table=version_table,
        scope=scope,
    )
    if stored_version is not None and stored_version > current_version:
        raise RuntimeError(
            f"Database schema scope {scope!r} is newer than this application supports"
        )

    starting_version = stored_version or 0
    for next_version in range(starting_version + 1, current_version + 1):
        migration = migrations.get(next_version)
        if migration is None:
            raise RuntimeError(
                f"Missing migration for schema scope {scope!r} version {next_version}"
            )
        migration(connection)
        _write_schema_version(
            connection,
            version_table=version_table,
            scope=scope,
            version=next_version,
        )

    return current_version


def migrate_state_store_schema(
    connection: Connection,
    *,
    state_metadata: MetaData,
    pending_session_claims_table: Table,
    interrupt_requests_table: Table,
    current_version: int = CURRENT_STATE_STORE_SCHEMA_VERSION,
) -> int:
    _SCHEMA_VERSION_METADATA.create_all(connection)
    state_metadata.create_all(connection)

    migrations: dict[int, Callable[[Connection], None]] = {
        1: lambda conn: _migration_1_add_interrupt_details_json(
            conn,
            interrupt_requests_table=interrupt_requests_table,
        ),
        2: lambda conn: _migration_2_add_pending_claim_expires_at(
            conn,
            pending_session_claims_table=pending_session_claims_table,
        ),
        3: lambda conn: _migration_3_add_lightweight_state_indexes(
            conn,
            pending_session_claims_table=pending_session_claims_table,
            interrupt_requests_table=interrupt_requests_table,
        ),
        4: lambda conn: _migration_4_add_interrupt_credential_id(
            conn,
            interrupt_requests_table=interrupt_requests_table,
        ),
        5: lambda conn: _migration_5_drop_legacy_pending_claim_rows(
            conn,
            pending_session_claims_table=pending_session_claims_table,
        ),
    }
    return _apply_schema_migrations(
        connection,
        version_table=_SCHEMA_VERSIONS,
        scope=STATE_STORE_SCHEMA_NAME,
        current_version=current_version,
        migrations=migrations,
    )


def migrate_task_store_schema(
    connection: Connection,
    *,
    task_table: Table,
    current_version: int = CURRENT_TASK_STORE_SCHEMA_VERSION,
) -> int:
    _SCHEMA_VERSION_METADATA.create_all(connection)
    task_table.metadata.create_all(connection, tables=[task_table])

    migrations: dict[int, Callable[[Connection], None]] = {
        1: lambda conn: _migration_1_add_task_store_sdk_columns(
            conn,
            task_table=task_table,
        ),
        2: lambda conn: _migration_2_backfill_task_store_sdk_defaults(
            conn,
            task_table=task_table,
        ),
        3: lambda conn: _migration_3_add_task_store_indexes(
            conn,
            task_table=task_table,
        ),
    }
    return _apply_schema_migrations(
        connection,
        version_table=_SCHEMA_VERSIONS,
        scope=TASK_STORE_SCHEMA_NAME,
        current_version=current_version,
        migrations=migrations,
    )

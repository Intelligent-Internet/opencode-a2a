from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from a2a.compat.v0_3.model_conversions import compat_task_model_to_core
from a2a.server.context import ServerCallContext
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.types import Task, TaskState
from google.protobuf.json_format import MessageToDict, ParseDict
from sqlalchemy import inspect, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..task_states import TERMINAL_TASK_STATES
from .database import redact_database_url_for_logs

_TERMINAL_TASK_STATE_VALUES = tuple(
    value
    for state in TERMINAL_TASK_STATES
    for value in (
        TaskState.Name(int(state)),
        TaskState.Name(int(state)).removeprefix("TASK_STATE_").lower(),
    )
)
_REQUIRED_TASK_MODEL_COLUMNS = frozenset(
    {
        "id",
        "context_id",
        "kind",
        "owner",
        "last_updated",
        "status",
        "artifacts",
        "history",
        "metadata",
        "protocol_version",
    }
)
_REQUIRED_SCHEMA_COLUMNS = frozenset({"owner", "last_updated", "protocol_version"})


class TaskStoreSchemaCompatibilityError(RuntimeError):
    pass


class TaskStoreSdkCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _DatabaseTaskStoreShape:
    session_maker: Any
    task_model: type[Any]
    owner_resolver: Callable[[ServerCallContext], str | None]
    model_to_core_conversion: Callable[[Any], Task] | None


class DatabaseTaskStoreCompat:
    def __init__(self, task_store: DatabaseTaskStore) -> None:
        self._task_store = task_store
        self._shape = _resolve_database_task_store_shape(task_store)

    async def initialize(self) -> None:
        await self._task_store.initialize()

    async def migrate_legacy_rows(self) -> None:
        """Normalize historical payloads before serving queries, in one transaction.

        The SDK schema migrations retain v0.3 JSON and leave last_updated empty.
        Read-time conversion alone cannot fix SQL status/time filters. The protocol
        marker makes this resumable and leaves current rows and ownership unchanged.
        """
        model = self._shape.task_model
        table = model.__table__
        async with self._shape.session_maker.begin() as session:
            after_id: str | None = None
            while True:
                statement = select(model).where(
                    or_(model.protocol_version.is_(None), model.protocol_version == "0.3")
                )
                if after_id is not None:
                    statement = statement.where(model.id > after_id)
                rows = (
                    (await session.execute(statement.order_by(model.id).limit(100))).scalars().all()
                )
                if not rows:
                    return
                for row in rows:
                    try:
                        task = _task_model_to_core(
                            row,
                            model_to_core_conversion=self._shape.model_to_core_conversion,
                        )
                    except Exception:
                        raise TaskStoreSchemaCompatibilityError(
                            "Cannot convert a legacy SDK task payload; restore the pre-upgrade "
                            "backup or repair the legacy data before restarting."
                        ) from None
                    values = _task_row_values(task, owner=row.owner)
                    if row.last_updated is not None:
                        values["last_updated"] = row.last_updated
                    await session.execute(
                        update(table).where(table.c.id == row.id).values(**values)
                    )
                after_id = rows[-1].id

    async def validate_schema(self) -> None:
        database_url = redact_database_url_for_logs(
            self._task_store.engine.url.render_as_string(hide_password=True)
        )
        table_name = self._shape.task_model.__table__.name
        async with self._task_store.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: _validate_sdk_task_table_schema(
                    sync_conn,
                    table_name=table_name,
                    required_index=f"idx_{table_name}_owner_last_updated",
                    database_url=database_url,
                )
            )

    async def atomic_save(
        self,
        task: Task,
        context: ServerCallContext,
    ) -> bool:
        await self.initialize()
        statement = _build_atomic_task_save_statement(
            task=task,
            owner=self._shape.owner_resolver(context),
            task_table=self._shape.task_model.__table__,
        )
        async with self._shape.session_maker.begin() as session:
            result = await session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def load_task(
        self,
        task_id: str,
        context: ServerCallContext,
    ) -> Task | None:
        await self.initialize()
        owner = self._shape.owner_resolver(context)
        async with self._shape.session_maker() as session:
            stmt = select(self._shape.task_model).where(
                self._shape.task_model.id == task_id,
                self._shape.task_model.owner == owner,
            )
            result = await session.execute(stmt)
            task_model = result.scalar_one_or_none()
        if task_model is None:
            return None
        return _task_model_to_core(
            task_model,
            model_to_core_conversion=self._shape.model_to_core_conversion,
        )


def _resolve_database_task_store_shape(
    task_store: DatabaseTaskStore,
) -> _DatabaseTaskStoreShape:
    missing: list[str] = []
    session_maker = getattr(task_store, "async_session_maker", None)
    if session_maker is None:
        missing.append("async_session_maker")

    task_model = getattr(task_store, "task_model", None)
    task_table = getattr(task_model, "__table__", None)
    if task_model is None or task_table is None:
        missing.append("task_model.__table__")
    elif missing_columns := sorted(_REQUIRED_TASK_MODEL_COLUMNS - set(task_table.columns.keys())):
        missing.append(f"task_model columns ({', '.join(missing_columns)})")

    owner_resolver = getattr(task_store, "owner_resolver", None)
    if not callable(owner_resolver):
        missing.append("owner_resolver")

    if missing:
        details = ", ".join(missing)
        raise TaskStoreSdkCompatibilityError(
            "DatabaseTaskStore shape drift detected; compat layer requires "
            f"{details}. Update src/opencode_a2a/server/task_store_sdk_compat.py "
            "for the installed a2a-sdk version."
        )

    model_to_core_conversion = getattr(task_store, "model_to_core_conversion", None)
    if model_to_core_conversion is not None and not callable(model_to_core_conversion):
        raise TaskStoreSdkCompatibilityError(
            "DatabaseTaskStore shape drift detected; model_to_core_conversion must be callable."
        )

    return _DatabaseTaskStoreShape(
        session_maker=session_maker,
        task_model=cast(type[Any], task_model),
        owner_resolver=cast(Callable[[ServerCallContext], str | None], owner_resolver),
        model_to_core_conversion=model_to_core_conversion,
    )


def _build_atomic_task_save_statement(
    *,
    task: Task,
    owner: str | None,
    task_table: Any,
):
    values = _task_row_values(task, owner=owner)
    status_state = task_table.c.status["state"].as_string()
    persist_guard = or_(
        task_table.c.status.is_(None),
        status_state.is_(None),
        status_state.not_in(_TERMINAL_TASK_STATE_VALUES),
    )
    return (
        sqlite_insert(task_table)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[task_table.c.id],
            set_={key: value for key, value in values.items() if key != "id"},
            where=persist_guard,
        )
        .returning(task_table.c.id)
    )


def _task_row_values(task: Task, *, owner: str | None) -> dict[str, Any]:
    return {
        "id": task.id,
        "context_id": task.context_id,
        "kind": "task",
        "owner": owner,
        "last_updated": (
            task.status.timestamp.ToDatetime() if task.status.HasField("timestamp") else None
        ),
        "status": MessageToDict(task.status),
        "artifacts": [MessageToDict(artifact) for artifact in task.artifacts],
        "history": [MessageToDict(message) for message in task.history],
        "metadata": MessageToDict(task.metadata) if task.metadata.fields else None,
        "protocol_version": "1.0",
    }


def _task_model_to_core(
    task_model: Any,
    *,
    model_to_core_conversion: Callable[[Any], Task] | None,
) -> Task:
    if model_to_core_conversion is not None:
        return model_to_core_conversion(task_model)

    if getattr(task_model, "protocol_version", None) == "1.0":
        task = Task(
            id=task_model.id,
            context_id=task_model.context_id,
        )
        if task_model.status:
            ParseDict(task_model.status, task.status)
        if task_model.artifacts:
            for artifact_dict in task_model.artifacts:
                artifact = task.artifacts.add()
                ParseDict(artifact_dict, artifact)
        if task_model.history:
            for message_dict in task_model.history:
                message = task.history.add()
                ParseDict(message_dict, message)
        if task_model.task_metadata:
            task.metadata.update(task_model.task_metadata)
        return task

    return compat_task_model_to_core(task_model)


def _validate_sdk_task_table_schema(
    connection: Any,
    *,
    table_name: str,
    required_index: str,
    database_url: str,
) -> None:
    inspector = inspect(connection)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    missing_columns = sorted(_REQUIRED_SCHEMA_COLUMNS - existing_columns)
    missing_indexes = [] if required_index in existing_indexes else [required_index]
    if not missing_columns and not missing_indexes:
        return

    details: list[str] = []
    if missing_columns:
        details.append(f"missing columns: {', '.join(missing_columns)}")
    if missing_indexes:
        details.append(f"missing indexes: {', '.join(missing_indexes)}")

    raise TaskStoreSchemaCompatibilityError(
        f"Legacy SDK task table schema detected for '{table_name}' "
        f"({'; '.join(details)}). Run "
        "the matching release's `a2a-db upgrade head` before starting the service; "
        "install this release with the `opencode-a2a[migrations]` extra. "
        "When adding owner, explicitly set "
        "`--add_columns_owner_last_updated-default-owner` to the intended auth principal. "
        f"Database: {database_url}. See docs/guide.md#upgrading-an-existing-database."
    )

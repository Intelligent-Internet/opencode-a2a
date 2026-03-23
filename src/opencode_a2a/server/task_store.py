from __future__ import annotations

from typing import TYPE_CHECKING, cast

from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore

from ..config import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_CUSTOM_TASK_MODELS: dict[str, object] = {}


class _ConfiguredDatabaseTaskStore(TaskStore):
    def __init__(
        self,
        *,
        engine: "AsyncEngine",
        create_table: bool,
        table_name: str,
    ) -> None:
        from a2a.server.models import TaskModel, create_task_model
        from a2a.server.tasks.database_task_store import DatabaseTaskStore
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        self._delegate = DatabaseTaskStore.__new__(DatabaseTaskStore)
        self._delegate.engine = engine
        self._delegate.async_session_maker = async_sessionmaker(
            engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        self._delegate.create_table = create_table
        self._delegate._initialized = False
        if table_name == "tasks":
            self._delegate.task_model = TaskModel
        else:
            task_model = cast("type[TaskModel] | None", _CUSTOM_TASK_MODELS.get(table_name))
            if task_model is None:
                task_model = create_task_model(table_name)
                _CUSTOM_TASK_MODELS[table_name] = task_model
            self._delegate.task_model = task_model

    @property
    def engine(self) -> "AsyncEngine":
        return self._delegate.engine

    async def initialize(self) -> None:
        await self._delegate.initialize()

    async def save(self, task, context=None) -> None:  # noqa: ANN001
        await self._delegate.save(task, context)

    async def get(self, task_id, context=None):  # noqa: ANN001
        return await self._delegate.get(task_id, context)

    async def delete(self, task_id, context=None) -> None:  # noqa: ANN001
        await self._delegate.delete(task_id, context)


def build_task_store(settings: Settings) -> TaskStore:
    if settings.a2a_task_store_backend == "memory":
        return InMemoryTaskStore()

    from sqlalchemy.ext.asyncio import create_async_engine

    database_url = cast(str, settings.a2a_task_store_database_url)
    engine = create_async_engine(database_url)
    return _ConfiguredDatabaseTaskStore(
        engine=engine,
        create_table=settings.a2a_task_store_create_table,
        table_name=settings.a2a_task_store_table_name,
    )


async def initialize_task_store(task_store: TaskStore) -> None:
    initialize = getattr(task_store, "initialize", None)
    if callable(initialize):
        await initialize()


async def close_task_store(task_store: TaskStore) -> None:
    engine = cast("AsyncEngine | None", getattr(task_store, "engine", None))
    if engine is not None:
        await engine.dispose()

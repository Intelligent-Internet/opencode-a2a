from __future__ import annotations

from typing import TYPE_CHECKING, cast

from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore

from ..config import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def build_task_store(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
) -> TaskStore:
    from a2a.server.tasks.database_task_store import DatabaseTaskStore

    if settings.a2a_task_store_backend == "memory":
        return InMemoryTaskStore()

    resolved_engine = engine or build_database_engine(settings)
    return DatabaseTaskStore(
        engine=resolved_engine,
    )


def build_database_engine(settings: Settings) -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    database_url = cast(str, settings.a2a_task_store_database_url)
    return create_async_engine(database_url)


async def initialize_task_store(task_store: TaskStore) -> None:
    initialize = getattr(task_store, "initialize", None)
    if callable(initialize):
        await initialize()

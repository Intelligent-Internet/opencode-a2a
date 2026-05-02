from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


def build_lifespan(
    *,
    database_engine,
    task_store_runtime,
    runtime_state_runtime,
    client_manager,
    upstream_client,
    persistence_summary: Mapping[str, object] | None = None,
):
    @asynccontextmanager
    async def lifespan(_app):
        if persistence_summary is not None:
            logger.info(
                "Lightweight persistence configured backend=%s scope=%s "
                "database_url=%s sqlite_tuning=%s",
                persistence_summary.get("backend", "unknown"),
                persistence_summary.get("scope", "unknown"),
                persistence_summary.get("database_url", "n/a"),
                persistence_summary.get("sqlite_tuning", "not_applicable"),
            )
        task_store_started = False
        runtime_state_started = False
        try:
            await task_store_runtime.startup()
            task_store_started = True
            await runtime_state_runtime.startup()
            runtime_state_started = True
            yield
        finally:
            await client_manager.close_all()
            await upstream_client.close()
            if runtime_state_started:
                await runtime_state_runtime.shutdown()
            if task_store_started:
                await task_store_runtime.shutdown()
            if database_engine is not None:
                await database_engine.dispose()

    return lifespan

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import AsyncExitStack, asynccontextmanager

logger = logging.getLogger(__name__)


def build_lifespan(
    *,
    request_handler,
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
            # ExitStack runs every callback even if an earlier cleanup raises.
            async with AsyncExitStack() as cleanup:
                if database_engine is not None:
                    cleanup.push_async_callback(database_engine.dispose)
                if task_store_started:
                    cleanup.push_async_callback(task_store_runtime.shutdown)
                if runtime_state_started:
                    cleanup.push_async_callback(runtime_state_runtime.shutdown)
                cleanup.push_async_callback(upstream_client.close)
                cleanup.push_async_callback(client_manager.close_all)
                cleanup.push_async_callback(request_handler.aclose)

    return lifespan

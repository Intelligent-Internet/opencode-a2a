from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest_asyncio.fixture(autouse=True)
async def dispose_app_resources(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None]:
    import opencode_a2a.server.application as app_module

    tracked_engines: dict[int, AsyncEngine] = {}
    tracked_handlers: list[app_module.OpencodeRequestHandler] = []
    original_handler = app_module.OpencodeRequestHandler
    original_build_database_engine = app_module.build_database_engine

    def _build_database_engine(settings):  # noqa: ANN001
        engine = original_build_database_engine(settings)
        tracked_engines[id(engine)] = engine
        return engine

    def _build_handler(*args, **kwargs):  # noqa: ANN002, ANN003
        handler = original_handler(*args, **kwargs)
        tracked_handlers.append(handler)
        return handler

    monkeypatch.setattr(app_module, "build_database_engine", _build_database_engine)
    monkeypatch.setattr(app_module, "OpencodeRequestHandler", _build_handler)
    yield

    # ASGITransport does not run lifespan. Match the application's shutdown order
    # so background writes cannot keep checked-out connections past loop teardown.
    async with AsyncExitStack() as cleanup:
        for engine in tracked_engines.values():
            cleanup.push_async_callback(engine.dispose)
        for handler in tracked_handlers:
            cleanup.push_async_callback(handler.aclose)

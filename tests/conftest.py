from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest_asyncio.fixture(autouse=True)
async def dispose_app_database_engines(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None]:
    import opencode_a2a.server.application as app_module

    tracked_engines: dict[int, AsyncEngine] = {}
    original_build_database_engine = app_module.build_database_engine

    def _build_database_engine(settings):  # noqa: ANN001
        engine = original_build_database_engine(settings)
        tracked_engines[id(engine)] = engine
        return engine

    monkeypatch.setattr(app_module, "build_database_engine", _build_database_engine)
    yield

    for engine in tracked_engines.values():
        await engine.dispose()

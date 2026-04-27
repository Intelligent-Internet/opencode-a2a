from __future__ import annotations

import asyncio
from collections.abc import Generator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(autouse=True)
def dispose_app_database_engines(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    import opencode_a2a.server.application as app_module

    tracked_engines: dict[int, AsyncEngine] = {}
    original_build_database_engine = app_module.build_database_engine

    def _build_database_engine(settings):  # noqa: ANN001
        engine = original_build_database_engine(settings)
        tracked_engines[id(engine)] = engine
        return engine

    monkeypatch.setattr(app_module, "build_database_engine", _build_database_engine)
    yield

    if not tracked_engines:
        return

    async def _dispose_tracked_engines() -> None:
        for engine in tracked_engines.values():
            await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_dispose_tracked_engines())
    finally:
        loop.close()

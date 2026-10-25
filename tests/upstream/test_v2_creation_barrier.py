import asyncio

import pytest

from opencode_a2a.upstream.contracts import ExecutionState
from opencode_a2a.upstream.v2_events import V2ExecutionUncertainError
from tests.upstream.test_v2_execution import NativeServer


@pytest.mark.asyncio
async def test_own_late_creation_event_does_not_block_first_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    try:
        await server.hub.start()
        server.feed.send(server.event("ses_one", "session.created"))
        async with asyncio.timeout(1):
            while server.hub.sessions["ses_one"].queue.empty():
                await asyncio.sleep(0)
        result = await server.engine.submit("ses_one", "first")
        assert result.outcome is not None and result.outcome.state == ExecutionState.COMPLETED
        assert len(server.inputs) == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unknown_baseline_work_still_blocks_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    try:
        await server.hub.start()
        server.feed.send(
            server.event("ses_one", "session.step.started", assistantMessageID="foreign")
        )
        async with asyncio.timeout(1):
            while server.hub.sessions["ses_one"].queue.empty():
                await asyncio.sleep(0)
        with pytest.raises(V2ExecutionUncertainError, match="outside"):
            await server.engine.submit("ses_one", "first")
        assert server.inputs == []
        assert server.hub.sessions["ses_one"].error is not None
    finally:
        await server.close()

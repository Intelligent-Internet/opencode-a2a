import asyncio

import pytest

from opencode_a2a.execution.executor import OpencodeAgentExecutor
from tests.support.helpers import DummyEventQueue, make_request_context
from tests.support.streaming_output import DummyStreamingClient


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_release_another_tasks_session_lock() -> None:
    client = DummyStreamingClient(stream_events_payload=[], response_text="ok", send_delay=0.2)
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    metadata = {"shared": {"session": {"id": "ses-shared"}}}
    owner = asyncio.create_task(
        executor.execute(
            make_request_context(
                task_id="owner", context_id="ctx-owner", text="first", metadata=metadata
            ),
            DummyEventQueue(),
        )
    )
    waiter = None
    try:
        async with asyncio.timeout(1):
            while client._in_flight_send == 0:
                await asyncio.sleep(0)
        lock = await executor._session_manager.get_session_lock("ses-shared")
        waiter = asyncio.create_task(
            executor.execute(
                make_request_context(
                    task_id="waiter", context_id="ctx-waiter", text="second", metadata=metadata
                ),
                DummyEventQueue(),
            )
        )
        async with asyncio.timeout(1):
            while ("waiter", "ctx-waiter") not in executor._running_requests:
                await asyncio.sleep(0)
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert lock.locked()
        assert not owner.done()
        await owner
        assert client.max_in_flight_send == 1
        assert not lock.locked()
    finally:
        owner.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(
            owner, *([waiter] if waiter is not None else []), return_exceptions=True
        )

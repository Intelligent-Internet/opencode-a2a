from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events.event_queue import EventQueue

from opencode_a2a_serve.agent import OpencodeAgentExecutor
from tests.helpers import make_request_context_mock


@pytest.mark.asyncio
async def test_execute_missing_ids():
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    # Mock RequestContext with missing IDs
    context = make_request_context_mock(
        task_id=None,
        context_id=None,
        call_context_enabled=False,
    )

    event_queue = AsyncMock(spec=EventQueue)

    # This should no longer raise RuntimeError
    await executor.execute(context, event_queue)

    # Verify that an event was enqueued
    event_queue.enqueue_event.assert_called()
    # For non-streaming, it should emit a Task
    args = event_queue.enqueue_event.call_args[0]
    from a2a.types import Task

    assert isinstance(args[0], Task)
    assert args[0].id == "unknown"
    assert args[0].status.state.name == "failed"


@pytest.mark.asyncio
async def test_cancel_missing_ids():
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    # Mock RequestContext with missing IDs
    context = make_request_context_mock(
        task_id=None,
        context_id=None,
    )

    event_queue = AsyncMock(spec=EventQueue)

    # This should no longer raise RuntimeError
    await executor.cancel(context, event_queue)

    # Verify that an event was enqueued and queue is not force-closed by executor.cancel
    event_queue.enqueue_event.assert_called()
    event_queue.close.assert_not_called()


@pytest.mark.asyncio
async def test_execute_invalid_metadata_type():
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    context = make_request_context_mock(
        task_id="task-1",
        context_id="ctx-1",
        user_input="hello",
        metadata=["not-a-map"],
        call_context_enabled=False,
    )

    event_queue = AsyncMock(spec=EventQueue)
    await executor.execute(context, event_queue)

    event_queue.enqueue_event.assert_called()
    from a2a.types import Task

    event = event_queue.enqueue_event.call_args[0][0]
    assert isinstance(event, Task)
    assert event.status.state.name == "failed"
    assert "Invalid metadata" in str(event.status.message)

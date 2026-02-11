from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events.event_queue import EventQueue

from opencode_a2a.agent import OpencodeAgentExecutor


@pytest.mark.asyncio
async def test_execute_missing_ids():
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    # Mock RequestContext with missing IDs
    context = MagicMock(spec=RequestContext)
    context.task_id = None
    context.context_id = None
    context.call_context = None

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
    context = MagicMock(spec=RequestContext)
    context.task_id = None
    context.context_id = None

    event_queue = AsyncMock(spec=EventQueue)

    # This should no longer raise RuntimeError
    await executor.cancel(context, event_queue)

    # Verify that an event was enqueued and queue was closed
    event_queue.enqueue_event.assert_called()
    event_queue.close.assert_called()


@pytest.mark.asyncio
async def test_execute_invalid_metadata_type():
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    context = MagicMock(spec=RequestContext)
    context.task_id = "task-1"
    context.context_id = "ctx-1"
    context.call_context = None
    context.get_user_input.return_value = "hello"
    context.metadata = ["not-a-map"]
    context.message = None
    context.current_task = None

    event_queue = AsyncMock(spec=EventQueue)
    await executor.execute(context, event_queue)

    event_queue.enqueue_event.assert_called()
    from a2a.types import Task

    event = event_queue.enqueue_event.call_args[0][0]
    assert isinstance(event, Task)
    assert event.status.state.name == "failed"
    assert "Invalid metadata" in str(event.status.message)

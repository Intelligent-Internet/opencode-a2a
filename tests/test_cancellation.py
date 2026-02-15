import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskState, TaskStatusUpdateEvent

from opencode_a2a_serve.agent import OpencodeAgentExecutor
from opencode_a2a_serve.opencode_client import OpencodeClient
from tests.helpers import configure_mock_client_runtime, make_request_context_mock


@pytest.mark.asyncio
async def test_cancel_interrupts_running_execute_and_keeps_queue_open():
    client = AsyncMock(spec=OpencodeClient)
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_message(
        session_id,
        _text,
        *,
        directory=None,  # noqa: ARG001
        timeout_override=None,  # noqa: ARG001
    ):
        send_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            send_cancelled.set()
            raise
        response = MagicMock()
        response.text = "OpenCode response"
        response.session_id = session_id
        response.message_id = "msg-1"
        return response

    client.create_session.return_value = "session-1"
    client.send_message.side_effect = send_message
    configure_mock_client_runtime(client)

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    execute_context = make_request_context_mock(
        task_id="task-1",
        context_id="context-A",
        identity="user-1",
        user_input="hello",
    )
    execute_queue = AsyncMock(spec=EventQueue)

    execute_task = asyncio.create_task(executor.execute(execute_context, execute_queue))
    await asyncio.wait_for(send_started.wait(), timeout=1.0)

    cancel_context = make_request_context_mock(
        task_id="task-1",
        context_id="context-A",
        call_context_enabled=False,
    )
    cancel_queue = AsyncMock(spec=EventQueue)

    await asyncio.wait_for(executor.cancel(cancel_context, cancel_queue), timeout=1.0)

    cancel_events = [call.args[0] for call in cancel_queue.enqueue_event.call_args_list]
    assert any(
        isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.canceled
        for event in cancel_events
    )
    cancel_queue.close.assert_not_called()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execute_task, timeout=1.0)

    assert send_cancelled.is_set()
    assert executor._sessions.get(("user-1", "context-A")) is None
    assert ("task-1", "context-A") not in executor._running_requests
    assert ("task-1", "context-A") not in executor._running_stop_events
    assert ("task-1", "context-A") not in executor._running_identities


@pytest.mark.asyncio
async def test_cancel_does_not_block_with_real_event_queue() -> None:
    executor = OpencodeAgentExecutor(MagicMock(), streaming_enabled=False)
    context = make_request_context_mock(
        task_id=None,
        context_id=None,
        call_context_enabled=False,
    )
    queue = EventQueue()

    await asyncio.wait_for(executor.cancel(context, queue), timeout=0.5)

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskState, TaskStatusUpdateEvent

from opencode_a2a_server.execution.executor import OpencodeAgentExecutor
from opencode_a2a_server.opencode_upstream_client import OpencodeUpstreamClient
from tests.helpers import configure_mock_client_runtime, make_request_context_mock


@pytest.mark.asyncio
async def test_cancel_interrupts_running_execute_and_keeps_queue_open(caplog):
    client = AsyncMock(spec=OpencodeUpstreamClient)
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_message(
        session_id,
        _text,
        *,
        directory=None,  # noqa: ARG001
        model_override=None,  # noqa: ARG001
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
    client.abort_session.return_value = True
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

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.execution.executor"):
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
    client.abort_session.assert_awaited_once_with("session-1", directory="/tmp/workspace")
    assert any("metric=a2a_cancel_requests_total" in record.message for record in caplog.records)
    assert any(
        "metric=a2a_cancel_abort_attempt_total" in record.message for record in caplog.records
    )
    assert any(
        "metric=a2a_cancel_abort_success_total" in record.message for record in caplog.records
    )
    assert any("metric=a2a_cancel_duration_ms" in record.message for record in caplog.records)
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


@pytest.mark.asyncio
async def test_cancel_keeps_canceled_status_when_abort_session_fails() -> None:
    client = AsyncMock(spec=OpencodeUpstreamClient)
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_message(
        session_id,
        _text,
        *,
        directory=None,  # noqa: ARG001
        model_override=None,  # noqa: ARG001
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

    client.create_session.return_value = "session-2"
    client.send_message.side_effect = send_message
    request = httpx.Request("POST", "http://opencode/session/session-2/abort")
    response = httpx.Response(404, request=request)
    client.abort_session.side_effect = httpx.HTTPStatusError(
        "not found",
        request=request,
        response=response,
    )
    configure_mock_client_runtime(client)

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    execute_context = make_request_context_mock(
        task_id="task-2",
        context_id="context-B",
        identity="user-2",
        user_input="hello",
    )
    execute_queue = AsyncMock(spec=EventQueue)
    execute_task = asyncio.create_task(executor.execute(execute_context, execute_queue))
    await asyncio.wait_for(send_started.wait(), timeout=1.0)

    cancel_context = make_request_context_mock(
        task_id="task-2",
        context_id="context-B",
        call_context_enabled=False,
    )
    cancel_queue = AsyncMock(spec=EventQueue)
    await asyncio.wait_for(executor.cancel(cancel_context, cancel_queue), timeout=1.0)

    cancel_events = [call.args[0] for call in cancel_queue.enqueue_event.call_args_list]
    assert any(
        isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.canceled
        for event in cancel_events
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execute_task, timeout=1.0)

    assert send_cancelled.is_set()
    client.abort_session.assert_awaited_once_with("session-2", directory="/tmp/workspace")


@pytest.mark.asyncio
async def test_cancel_remains_responsive_when_abort_session_hangs(caplog) -> None:
    client = AsyncMock(spec=OpencodeUpstreamClient)
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_message(
        session_id,
        _text,
        *,
        directory=None,  # noqa: ARG001
        model_override=None,  # noqa: ARG001
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

    async def slow_abort_session(
        _session_id: str,
        *,
        directory: str | None = None,  # noqa: ARG001
    ) -> bool:
        await asyncio.sleep(10)
        return True

    client.create_session.return_value = "session-3"
    client.send_message.side_effect = send_message
    client.abort_session.side_effect = slow_abort_session
    configure_mock_client_runtime(client)

    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=False,
        cancel_abort_timeout_seconds=0.05,
    )
    execute_context = make_request_context_mock(
        task_id="task-3",
        context_id="context-C",
        identity="user-3",
        user_input="hello",
    )
    execute_queue = AsyncMock(spec=EventQueue)
    execute_task = asyncio.create_task(executor.execute(execute_context, execute_queue))
    await asyncio.wait_for(send_started.wait(), timeout=1.0)

    cancel_context = make_request_context_mock(
        task_id="task-3",
        context_id="context-C",
        call_context_enabled=False,
    )
    cancel_queue = AsyncMock(spec=EventQueue)
    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.execution.executor"):
        await asyncio.wait_for(executor.cancel(cancel_context, cancel_queue), timeout=0.5)

    cancel_events = [call.args[0] for call in cancel_queue.enqueue_event.call_args_list]
    assert any(
        isinstance(event, TaskStatusUpdateEvent) and event.status.state == TaskState.canceled
        for event in cancel_events
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(execute_task, timeout=1.0)
    assert send_cancelled.is_set()
    client.abort_session.assert_awaited_once_with("session-3", directory="/tmp/workspace")
    assert any(
        "metric=a2a_cancel_abort_timeout_total" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_cancel_waiting_for_session_lock_does_not_abort_other_generation() -> None:
    client = AsyncMock(spec=OpencodeUpstreamClient)
    client.create_session.return_value = "session-4"
    client.send_message = AsyncMock()
    client.abort_session.return_value = True
    configure_mock_client_runtime(client)

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    prelocked_session_lock = asyncio.Lock()
    await prelocked_session_lock.acquire()
    executor._session_locks["session-4"] = prelocked_session_lock

    execute_context = make_request_context_mock(
        task_id="task-4",
        context_id="context-D",
        identity="user-4",
        user_input="hello",
    )
    execute_queue = AsyncMock(spec=EventQueue)
    execute_task = asyncio.create_task(executor.execute(execute_context, execute_queue))

    try:
        for _ in range(20):
            if executor._sessions.get(("user-4", "context-D")) == "session-4":
                break
            await asyncio.sleep(0.01)

        cancel_context = make_request_context_mock(
            task_id="task-4",
            context_id="context-D",
            call_context_enabled=False,
        )
        cancel_queue = AsyncMock(spec=EventQueue)
        await asyncio.wait_for(executor.cancel(cancel_context, cancel_queue), timeout=1.0)

        client.abort_session.assert_not_awaited()
        client.send_message.assert_not_awaited()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execute_task, timeout=1.0)
    finally:
        if prelocked_session_lock.locked():
            prelocked_session_lock.release()

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.events.event_queue import EventQueue
from a2a.types import Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent

from opencode_a2a_server.execution.executor import OpencodeAgentExecutor
from opencode_a2a_server.opencode_upstream_client import OpencodeMessage, UpstreamContractError
from tests.support.helpers import configure_mock_client_runtime, make_request_context_mock


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_type", "expected_state"),
    [
        (400, "UPSTREAM_BAD_REQUEST", TaskState.failed),
        (401, "UPSTREAM_UNAUTHORIZED", TaskState.auth_required),
        (403, "UPSTREAM_PERMISSION_DENIED", TaskState.failed),
        (429, "UPSTREAM_QUOTA_EXCEEDED", TaskState.failed),
        (500, "UPSTREAM_SERVER_ERROR", TaskState.failed),
    ],
)
async def test_execute_http_error_maps_to_task_error_type_and_state(
    status: int, expected_type: str, expected_state: TaskState
) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:4096/message")
    response = httpx.Response(
        status_code=status,
        request=request,
        json={"detail": "upstream rejected"},
    )
    exc = httpx.HTTPStatusError("upstream error", request=request, response=response)

    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    client.create_session = create_session
    client.send_message = AsyncMock(side_effect=exc)
    configure_mock_client_runtime(client, directory="/tmp/workspace")

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    context = make_request_context_mock(
        task_id="task-1",
        context_id="ctx-1",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    event = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if isinstance(payload, Task):
            event = payload
            break
    assert event is not None
    assert event.status.state == expected_state
    assert event.metadata is not None
    assert event.metadata["opencode"]["error"]["type"] == expected_type
    assert event.metadata["opencode"]["error"]["upstream_status"] == status


@pytest.mark.asyncio
async def test_streaming_execute_http_error_emits_status_update_with_metadata() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:4096/message")
    response = httpx.Response(
        status_code=429,
        request=request,
        json={"error": "rate limit"},
    )
    exc = httpx.HTTPStatusError("upstream error", request=request, response=response)

    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    client.create_session = create_session
    client.send_message = AsyncMock(side_effect=exc)
    configure_mock_client_runtime(client, directory="/tmp/workspace")
    client.stream_timeout = None
    client.directory = None

    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda _context: True  # noqa: E731
    context = make_request_context_mock(
        task_id="task-stream",
        context_id="ctx-stream",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    status = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if (
            isinstance(payload, TaskStatusUpdateEvent)
            and payload.final
            and payload.metadata is not None
            and payload.metadata.get("opencode", {}).get("error", {}).get("type")
            == "UPSTREAM_QUOTA_EXCEEDED"
        ):
            status = payload
            break

    assert status is not None
    assert status.status.state == TaskState.failed
    assert status.metadata["opencode"]["error"]["type"] == "UPSTREAM_QUOTA_EXCEEDED"
    assert status.metadata["opencode"]["error"]["upstream_status"] == 429


@pytest.mark.asyncio
async def test_execute_upstream_payload_error_maps_to_task_error_type() -> None:
    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    client.create_session = create_session
    client.send_message = AsyncMock(
        side_effect=UpstreamContractError(
            "OpenCode /session/{sessionID}/message returned non-JSON response "
            "(status=200, content-type=text/plain, body=ProviderModelNotFoundError)"
        )
    )
    configure_mock_client_runtime(client, directory="/tmp/workspace")

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    context = make_request_context_mock(
        task_id="task-payload",
        context_id="ctx-payload",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    event = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if isinstance(payload, Task):
            event = payload
            break

    assert event is not None
    assert event.status.state == TaskState.failed
    assert event.metadata is not None
    assert event.metadata["opencode"]["error"]["type"] == "UPSTREAM_PAYLOAD_ERROR"
    assert "payload mismatch" in event.status.message.parts[0].root.text


@pytest.mark.asyncio
async def test_execute_response_info_error_maps_to_task_failed_state() -> None:
    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    client.create_session = create_session
    client.send_message = AsyncMock(
        return_value=OpencodeMessage(
            text="",
            session_id="ses-1",
            message_id="msg-1",
            raw={
                "info": {
                    "id": "msg-1",
                    "sessionID": "ses-1",
                    "role": "assistant",
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "API key expired. Please renew the API key.",
                            "statusCode": 400,
                        },
                    },
                }
            },
        )
    )
    configure_mock_client_runtime(client, directory="/tmp/workspace")

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    context = make_request_context_mock(
        task_id="task-response-error",
        context_id="ctx-response-error",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    event = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if isinstance(payload, Task):
            event = payload
            break

    assert event is not None
    assert event.status.state == TaskState.failed
    assert event.metadata is not None
    assert event.metadata["opencode"]["error"]["type"] == "UPSTREAM_BAD_REQUEST"
    assert event.metadata["opencode"]["error"]["upstream_status"] == 400
    assert "API key expired" in event.status.message.parts[0].root.text


@pytest.mark.asyncio
async def test_streaming_execute_response_info_error_emits_failed_status_and_error_artifact() -> (
    None
):
    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    async def stream_events(stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del stop_event, directory
        yield {"type": "server.connected", "properties": {}}

    client.create_session = create_session
    client.send_message = AsyncMock(
        return_value=OpencodeMessage(
            text="",
            session_id="ses-1",
            message_id="msg-1",
            raw={
                "info": {
                    "id": "msg-1",
                    "sessionID": "ses-1",
                    "role": "assistant",
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "API key expired. Please renew the API key.",
                            "statusCode": 400,
                        },
                    },
                }
            },
        )
    )
    client.stream_events = stream_events
    client.stream_timeout = None
    client.directory = None
    configure_mock_client_runtime(client, directory="/tmp/workspace")

    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda _context: True  # noqa: E731
    context = make_request_context_mock(
        task_id="task-stream-response-error",
        context_id="ctx-stream-response-error",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    final_status = None
    error_artifact = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if isinstance(payload, TaskArtifactUpdateEvent):
            error_artifact = payload
        if isinstance(payload, TaskStatusUpdateEvent) and payload.final:
            final_status = payload

    assert error_artifact is not None
    assert "API key expired" in error_artifact.artifact.parts[0].root.text
    assert final_status is not None
    assert final_status.status.state == TaskState.failed
    assert final_status.metadata is not None
    assert final_status.metadata["opencode"]["error"]["type"] == "UPSTREAM_BAD_REQUEST"
    assert final_status.metadata["opencode"]["error"]["upstream_status"] == 400


@pytest.mark.asyncio
async def test_streaming_execute_session_error_emits_failed_status() -> None:
    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    async def send_message(
        session_id: str,
        text: str | None = None,
        *,
        parts=None,  # noqa: ANN001
        directory: str | None = None,
        model_override=None,  # noqa: ANN001
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del session_id, text, parts, directory, model_override, timeout_override
        await asyncio.sleep(0.01)
        return OpencodeMessage(
            text="",
            session_id="ses-1",
            message_id="msg-1",
            raw={},
        )

    async def stream_events(stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del stop_event, directory
        yield {
            "type": "session.error",
            "properties": {
                "sessionID": "ses-1",
                "error": {
                    "name": "APIError",
                    "data": {
                        "message": "API key expired. Please renew the API key.",
                        "statusCode": 400,
                    },
                },
            },
        }

    client.create_session = create_session
    client.send_message = send_message
    client.stream_events = stream_events
    client.stream_timeout = None
    client.directory = None
    configure_mock_client_runtime(client, directory="/tmp/workspace")

    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda _context: True  # noqa: E731
    context = make_request_context_mock(
        task_id="task-stream-session-error",
        context_id="ctx-stream-session-error",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    final_status = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if isinstance(payload, TaskStatusUpdateEvent) and payload.final:
            final_status = payload

    assert final_status is not None
    assert final_status.status.state == TaskState.failed
    assert final_status.metadata is not None
    assert final_status.metadata["opencode"]["error"]["type"] == "UPSTREAM_BAD_REQUEST"
    assert final_status.metadata["opencode"]["error"]["upstream_status"] == 400


@pytest.mark.asyncio
async def test_streaming_execute_message_updated_info_error_emits_failed_status() -> None:
    client = AsyncMock()

    async def create_session(title: str | None = None, *, directory: str | None = None) -> str:
        del title, directory
        return "ses-1"

    async def send_message(
        session_id: str,
        text: str | None = None,
        *,
        parts=None,  # noqa: ANN001
        directory: str | None = None,
        model_override=None,  # noqa: ANN001
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del session_id, text, parts, directory, model_override, timeout_override
        await asyncio.sleep(0.01)
        return OpencodeMessage(
            text="",
            session_id="ses-1",
            message_id="msg-1",
            raw={},
        )

    async def stream_events(stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del stop_event, directory
        yield {
            "type": "message.updated",
            "properties": {
                "info": {
                    "sessionID": "ses-1",
                    "error": {
                        "name": "APIError",
                        "data": {
                            "message": "API key expired. Please renew the API key.",
                            "statusCode": 400,
                        },
                    },
                }
            },
        }
        yield {"type": "session.idle", "properties": {"sessionID": "ses-1"}}

    client.create_session = create_session
    client.send_message = send_message
    client.stream_events = stream_events
    client.stream_timeout = None
    client.directory = None
    configure_mock_client_runtime(client, directory="/tmp/workspace")

    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda _context: True  # noqa: E731
    context = make_request_context_mock(
        task_id="task-stream-message-updated-error",
        context_id="ctx-stream-message-updated-error",
        user_input="hello",
        call_context_enabled=False,
    )
    event_queue = AsyncMock(spec=EventQueue)

    await executor.execute(context, event_queue)

    final_status = None
    for call in event_queue.enqueue_event.call_args_list:
        payload = call.args[0]
        if isinstance(payload, TaskStatusUpdateEvent) and payload.final:
            final_status = payload

    assert final_status is not None
    assert final_status.status.state == TaskState.failed
    assert final_status.metadata is not None
    assert final_status.metadata["opencode"]["error"]["type"] == "UPSTREAM_BAD_REQUEST"
    assert final_status.metadata["opencode"]["error"]["upstream_status"] == 400

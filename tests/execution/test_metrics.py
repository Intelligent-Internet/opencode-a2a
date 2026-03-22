from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import Message, MessageSendParams, Role, Task, TaskState, TaskStatus, TextPart

from opencode_a2a.execution.executor import OpencodeAgentExecutor, _StreamOutputState
from opencode_a2a.server.application import OpencodeRequestHandler
from tests.support.helpers import DummyEventQueue, make_settings


def _make_message_send_params() -> MessageSendParams:
    return MessageSendParams(
        message=Message(
            message_id="msg-user-1",
            role=Role.user,
            parts=[TextPart(text="hello")],
        )
    )


@pytest.mark.asyncio
async def test_stream_request_metrics_track_total_and_active(caplog) -> None:
    class _FakeAggregator:
        async def consume_and_emit(self, _consumer):
            yield Task(
                id="task-1",
                context_id="ctx-1",
                status=TaskStatus(state=TaskState.working),
            )
            await asyncio.sleep(10)

    class _TestHandler(OpencodeRequestHandler):
        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            queue = AsyncMock()
            producer_task = asyncio.create_task(asyncio.sleep(10))
            self._producer_task = producer_task
            self._queue = queue
            return MagicMock(), "task-1", queue, _FakeAggregator(), producer_task

        async def _cleanup_producer(self, producer_task, task_id):  # noqa: ANN001
            del task_id
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass

    handler = _TestHandler(agent_executor=MagicMock(), task_store=InMemoryTaskStore())

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.execution.executor"):
        stream = handler.on_message_send_stream(_make_message_send_params())
        first_event = await stream.__anext__()
        assert isinstance(first_event, Task)
        await stream.aclose()
        await asyncio.sleep(0)

    messages = [record.message for record in caplog.records]
    assert any("metric=a2a_stream_requests_total" in message for message in messages)
    assert any("metric=a2a_stream_active value=1.0" in message for message in messages)
    assert any("metric=a2a_stream_active value=-1" in message for message in messages)


@pytest.mark.asyncio
async def test_streaming_metrics_capture_tool_call_and_interrupt_events(caplog) -> None:
    class _Client:
        def __init__(self) -> None:
            self.stream_timeout = None
            self.directory = None
            self.settings = make_settings(
                a2a_bearer_token="test",
                opencode_base_url="http://localhost",
            )
            self._interrupt_requests: dict[str, str] = {}

        async def stream_events(self, stop_event=None, *, directory=None):  # noqa: ANN001
            del stop_event, directory
            yield {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-1",
                    "sessionID": "ses-1",
                    "permission": "read",
                    "patterns": ["/tmp/secret"],
                    "always": [],
                },
            }
            yield {
                "type": "permission.replied",
                "properties": {
                    "requestID": "perm-1",
                    "sessionID": "ses-1",
                },
            }
            yield {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "part-tool-1",
                        "sessionID": "ses-1",
                        "messageID": "msg-1",
                        "type": "tool",
                        "role": "assistant",
                        "callID": "call-1",
                        "tool": "bash",
                        "state": {"status": "running"},
                    }
                },
            }

        def remember_interrupt_request(
            self,
            *,
            request_id: str,
            session_id: str,
            interrupt_type: str | None = None,
            identity: str | None = None,
            task_id: str | None = None,
            context_id: str | None = None,
            ttl_seconds: float | None = None,
        ) -> None:
            del interrupt_type, identity, task_id, context_id, ttl_seconds
            self._interrupt_requests[request_id] = session_id

        def discard_interrupt_request(self, request_id: str) -> None:
            self._interrupt_requests.pop(request_id, None)

    executor = OpencodeAgentExecutor(_Client(), streaming_enabled=True)
    terminal_signal = asyncio.get_running_loop().create_future()

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.execution.executor"):
        await executor._consume_opencode_stream(
            session_id="ses-1",
            identity="user-1",
            task_id="task-1",
            context_id="ctx-1",
            artifact_id="task-1:stream",
            stream_state=_StreamOutputState(
                user_text="hello",
                stable_message_id="task-1:ctx-1:assistant",
                event_id_namespace="task-1:ctx-1:task-1:stream",
            ),
            event_queue=DummyEventQueue(),
            stop_event=asyncio.Event(),
            terminal_signal=terminal_signal,
        )

    messages = [record.message for record in caplog.records]
    assert any("metric=interrupt_requests_total" in message for message in messages)
    assert any("metric=interrupt_resolved_total" in message for message in messages)
    assert any("metric=tool_call_chunks_emitted_total" in message for message in messages)


@pytest.mark.asyncio
async def test_streaming_retry_metric_increments_once_per_retry(monkeypatch, caplog) -> None:
    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0
            self.stream_timeout = None
            self.directory = None
            self.settings = make_settings(
                a2a_bearer_token="test",
                opencode_base_url="http://localhost",
            )

        async def stream_events(self, stop_event=None, *, directory=None):  # noqa: ANN001
            del stop_event, directory
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            for _ in ():
                yield {}

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("opencode_a2a.execution.executor.asyncio.sleep", _fast_sleep)

    executor = OpencodeAgentExecutor(_FlakyClient(), streaming_enabled=True)
    terminal_signal = asyncio.get_running_loop().create_future()

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.execution.executor"):
        await executor._consume_opencode_stream(
            session_id="ses-1",
            identity="user-1",
            task_id="task-1",
            context_id="ctx-1",
            artifact_id="task-1:stream",
            stream_state=_StreamOutputState(
                user_text="hello",
                stable_message_id="task-1:ctx-1:assistant",
                event_id_namespace="task-1:ctx-1:task-1:stream",
            ),
            event_queue=DummyEventQueue(),
            stop_event=asyncio.Event(),
            terminal_signal=terminal_signal,
        )

    messages = [record.message for record in caplog.records]
    assert sum("metric=opencode_stream_retries_total" in message for message in messages) == 1

import asyncio

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.types import Message, MessageSendParams, Role, TaskArtifactUpdateEvent, TextPart

from opencode_a2a_serve.agent import OpencodeAgentExecutor
from opencode_a2a_serve.config import Settings
from opencode_a2a_serve.opencode_client import OpencodeMessage


class DummyEventQueue:
    def __init__(self) -> None:
        self.events = []

    async def enqueue_event(self, event) -> None:  # noqa: ANN001
        self.events.append(event)

    async def close(self) -> None:
        return None


class DummyStreamingClient:
    def __init__(
        self,
        *,
        stream_events_payload: list[dict],
        response_text: str,
        response_message_id: str = "msg-1",
        send_delay: float = 0.02,
    ) -> None:
        self._stream_events_payload = stream_events_payload
        self._response_text = response_text
        self._response_message_id = response_message_id
        self._send_delay = send_delay
        self._in_flight_send = 0
        self.max_in_flight_send = 0
        self.stream_timeout = None
        self.directory = None
        self.settings = Settings(
            A2A_BEARER_TOKEN="test",
            OPENCODE_BASE_URL="http://localhost",
        )

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        return "ses-1"

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del text, directory, timeout_override
        self._in_flight_send += 1
        self.max_in_flight_send = max(self.max_in_flight_send, self._in_flight_send)
        await asyncio.sleep(self._send_delay)
        self._in_flight_send -= 1
        return OpencodeMessage(
            text=self._response_text,
            session_id=session_id,
            message_id=self._response_message_id,
            raw={},
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del directory
        for event in self._stream_events_payload:
            if stop_event and stop_event.is_set():
                break
            await asyncio.sleep(0)
            yield event


def _context(
    *, task_id: str, context_id: str, text: str, metadata: dict | None = None
) -> RequestContext:
    message = Message(
        message_id="req-1",
        role=Role.user,
        parts=[TextPart(text=text)],
    )
    params = MessageSendParams(message=message, metadata=metadata)
    return RequestContext(request=params, task_id=task_id, context_id=context_id)


def _event(
    *,
    session_id: str,
    role: str | None,
    part_type: str,
    delta: str,
    message_id: str | None = "msg-1",
) -> dict:
    properties: dict = {
        "part": {
            "sessionID": session_id,
            "type": part_type,
        },
        "delta": delta,
    }
    if role is not None:
        properties["part"]["role"] = role
    if message_id is not None:
        properties["part"]["messageID"] = message_id
    return {
        "type": "message.part.updated",
        "properties": properties,
    }


def _artifact_updates(queue: DummyEventQueue) -> list[TaskArtifactUpdateEvent]:
    return [event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)]


def _part_text(event: TaskArtifactUpdateEvent) -> str:
    part = event.artifact.parts[0]
    return getattr(part, "text", None) or getattr(part.root, "text", "")


@pytest.mark.asyncio
async def test_streaming_filters_user_echo_and_emits_structured_channels() -> None:
    user_text = "who are you"
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="ROLE_USER", part_type="text", delta=user_text),
            _event(session_id="ses-1", role="assistant", part_type="reasoning", delta="thinking"),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool_call",
                delta='{"tool":"search"}',
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="final answer"),
        ],
        response_text="final answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(_context(task_id="task-1", context_id="ctx-1", text=user_text), queue)

    updates = _artifact_updates(queue)
    assert updates
    texts = [_part_text(event) for event in updates]
    assert user_text not in texts
    channels = [event.artifact.metadata["opencode"]["channel"] for event in updates]
    assert _unique(channels) == ["reasoning", "tool_call", "final_answer"]


@pytest.mark.asyncio
async def test_streaming_does_not_send_duplicate_final_snapshot_when_chunks_exist() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="stable final answer",
            ),
        ],
        response_text="stable final answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(_context(task_id="task-2", context_id="ctx-2", text="hi"), queue)

    final_updates = [
        event
        for event in _artifact_updates(queue)
        if event.artifact.metadata["opencode"]["channel"] == "final_answer"
    ]
    assert len(final_updates) == 1
    assert _part_text(final_updates[0]) == "stable final answer"
    assert final_updates[0].artifact.metadata["opencode"]["source"] != "final_snapshot"


@pytest.mark.asyncio
async def test_streaming_emits_final_snapshot_only_when_stream_has_no_final_answer() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="reasoning", delta="plan step"),
        ],
        response_text="final answer from send_message",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(_context(task_id="task-3", context_id="ctx-3", text="hello"), queue)

    final_updates = [
        event
        for event in _artifact_updates(queue)
        if event.artifact.metadata["opencode"]["channel"] == "final_answer"
    ]
    assert len(final_updates) == 1
    final_event = final_updates[0]
    assert _part_text(final_event) == "final answer from send_message"
    assert final_event.artifact.metadata["opencode"]["source"] == "final_snapshot"
    assert final_event.append is False
    assert final_event.last_chunk is True


@pytest.mark.asyncio
async def test_execute_serializes_send_message_per_session() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[],
        response_text="ok",
        send_delay=0.05,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    queue_1 = DummyEventQueue()
    queue_2 = DummyEventQueue()
    metadata = {"opencode_session_id": "ses-shared"}

    await asyncio.gather(
        executor.execute(
            _context(task_id="task-4", context_id="ctx-4", text="hello", metadata=metadata), queue_1
        ),
        executor.execute(
            _context(task_id="task-5", context_id="ctx-5", text="world", metadata=metadata), queue_2
        ),
    )

    assert client.max_in_flight_send == 1


@pytest.mark.asyncio
async def test_streaming_drops_events_without_message_id_and_falls_back_to_snapshot() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="stream chunk without id",
                message_id=None,
            ),
        ],
        response_text="final answer from send_message",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(_context(task_id="task-6", context_id="ctx-6", text="hello"), queue)

    updates = _artifact_updates(queue)
    assert len(updates) == 1
    update = updates[0]
    assert _part_text(update) == "final answer from send_message"
    assert update.artifact.metadata["opencode"]["source"] == "final_snapshot"
    assert update.artifact.metadata["opencode"]["channel"] == "final_answer"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered

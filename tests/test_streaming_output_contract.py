import asyncio
import logging
from types import SimpleNamespace

import pytest
from a2a.types import (
    FilePart,
    FileWithUri,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)

from opencode_a2a_server.agent import (
    BlockType,
    OpencodeAgentExecutor,
    _extract_token_usage,
    _extract_tool_part_payload,
    _StreamOutputState,
)
from opencode_a2a_server.opencode_client import OpencodeMessage
from tests.helpers import (
    DummyEventQueue,
    make_request_context,
    make_request_context_with_parts,
    make_settings,
)


class DummyStreamingClient:
    def __init__(
        self,
        *,
        stream_events_payload: list[dict],
        response_text: str,
        response_message_id: str | None = "msg-1",
        response_raw: dict | None = None,
        send_delay: float = 0.02,
        stream_event_delays: list[float] | None = None,
        auto_idle: bool = True,
    ) -> None:
        self._stream_events_payload = stream_events_payload
        self._response_text = response_text
        self._response_message_id = response_message_id
        self._response_raw = response_raw or {}
        self._send_delay = send_delay
        self._stream_event_delays = stream_event_delays or []
        self._auto_idle = auto_idle
        self._in_flight_send = 0
        self.max_in_flight_send = 0
        self.stream_timeout = None
        self.directory = None
        self._interrupt_sessions: dict[str, str] = {}
        self.settings = make_settings(
            a2a_bearer_token="test",
            opencode_base_url="http://localhost",
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
        text: str | None = None,
        *,
        parts: list[dict] | None = None,
        directory: str | None = None,
        model_override: dict[str, str] | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del text, parts, directory, model_override, timeout_override
        self._in_flight_send += 1
        self.max_in_flight_send = max(self.max_in_flight_send, self._in_flight_send)
        await asyncio.sleep(self._send_delay)
        self._in_flight_send -= 1
        return OpencodeMessage(
            text=self._response_text,
            session_id=session_id,
            message_id=self._response_message_id,
            raw=self._response_raw,
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del directory
        for index, event in enumerate(self._stream_events_payload):
            if stop_event and stop_event.is_set():
                break
            delay = (
                self._stream_event_delays[index] if index < len(self._stream_event_delays) else 0
            )
            await asyncio.sleep(delay)
            yield event
        if self._auto_idle and not any(
            event.get("type") in {"session.idle", "session.error"}
            for event in self._stream_events_payload
        ):
            yield {"type": "session.idle", "properties": {"sessionID": "ses-1"}}

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
        self._interrupt_sessions[request_id] = session_id

    def resolve_interrupt_session(self, request_id: str) -> str | None:
        return self._interrupt_sessions.get(request_id)

    def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_sessions.pop(request_id, None)


def _event(
    *,
    session_id: str,
    role: str | None,
    part_type: str,
    delta: str,
    message_id: str | None = "msg-1",
    part_id: str | None = None,
    text: str | None = None,
    part_overrides: dict | None = None,
) -> dict:
    resolved_part_id = part_id or f"prt-{message_id or 'missing'}-{part_type}"
    properties: dict = {
        "part": {
            "id": resolved_part_id,
            "sessionID": session_id,
            "type": part_type,
        },
        "delta": delta,
    }
    if role is not None:
        properties["part"]["role"] = role
    if message_id is not None:
        properties["part"]["messageID"] = message_id
    if text is not None:
        properties["part"]["text"] = text
    if part_overrides:
        properties["part"].update(part_overrides)
    return {
        "type": "message.part.updated",
        "properties": properties,
    }


def _delta_event(
    *,
    session_id: str,
    part_id: str,
    delta: str,
    message_id: str | None = "msg-1",
) -> dict:
    properties: dict = {
        "sessionID": session_id,
        "partID": part_id,
        "field": "text",
        "delta": delta,
    }
    if message_id is not None:
        properties["messageID"] = message_id
    return {
        "type": "message.part.delta",
        "properties": properties,
    }


def _step_finish_usage_event(
    *,
    session_id: str,
    message_id: str = "msg-1",
    part_id: str = "prt-step-finish",
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost: float,
) -> dict:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": part_id,
                "sessionID": session_id,
                "messageID": message_id,
                "type": "step-finish",
                "reason": "stop",
                "cost": cost,
                "tokens": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": total_tokens,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            }
        },
    }


def _permission_asked_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "permission.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "permission": "read",
            "patterns": ["/data/project/.env.secret"],
            "always": ["/data/project/.env.example"],
            "metadata": {"path": "/data/project/.env.secret"},
            "tool": {"messageID": "msg-tool-1", "callID": "call-tool-1"},
        },
    }


def _question_asked_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "question.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "questions": [
                {
                    "header": "Confirm",
                    "question": "Proceed?",
                    "options": [{"label": "Yes", "value": "yes"}],
                }
            ],
        },
    }


def _interrupt_resolved_event(*, session_id: str, request_id: str, event_type: str) -> dict:
    return {
        "type": event_type,
        "properties": {
            "requestID": request_id,
            "sessionID": session_id,
        },
    }


def _artifact_updates(queue: DummyEventQueue) -> list[TaskArtifactUpdateEvent]:
    return [event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)]


def _part_text(event: TaskArtifactUpdateEvent) -> str:
    part = event.artifact.parts[0]
    return getattr(part, "text", None) or getattr(part.root, "text", "")


def _part_data(event: TaskArtifactUpdateEvent) -> dict:
    part = event.artifact.parts[0]
    return getattr(part, "data", None) or getattr(part.root, "data", {})


def _artifact_stream_meta(event: TaskArtifactUpdateEvent) -> dict:
    return event.artifact.metadata["shared"]["stream"]


def _status_shared_meta(event: TaskStatusUpdateEvent) -> dict:
    return (event.metadata or {})["shared"]


def _interrupt_meta(event: TaskStatusUpdateEvent) -> dict:
    return _status_shared_meta(event)["interrupt"]


@pytest.mark.asyncio
async def test_streaming_accepts_file_input_without_breaking_contract() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[],
        response_text="final answer from send_message",
        response_message_id="msg-1",
    )
    executor = OpencodeAgentExecutor(client=client, streaming_enabled=True)
    queue = DummyEventQueue()
    context = make_request_context_with_parts(
        task_id="task-1",
        context_id="ctx-1",
        parts=[
            FilePart(
                file=FileWithUri(
                    uri="file:///tmp/report.pdf",
                    mimeType="application/pdf",
                    name="report.pdf",
                )
            )
        ],
        call_context=SimpleNamespace(state={"a2a_streaming_request": True}),
    )

    await executor.execute(context, queue)

    status_events = [event for event in queue.events if isinstance(event, TaskStatusUpdateEvent)]

    assert status_events[-1].final is True
    assert status_events[-1].status.state == TaskState.completed


def test_stream_output_state_deduplicates_non_accumulating_tool_chunks() -> None:
    state = _StreamOutputState(
        user_text="",
        stable_message_id="msg-stable",
        event_id_namespace="task:ctx:stream",
    )

    assert state.register_chunk(
        block_type=BlockType.TOOL_CALL,
        content_key='{"status":"pending"}',
        append=False,
        accumulate_content=False,
    ) == (True, False)
    assert state.register_chunk(
        block_type=BlockType.TOOL_CALL,
        content_key='{"status":"pending"}',
        append=True,
        accumulate_content=False,
    ) == (False, False)
    assert state.register_chunk(
        block_type=BlockType.TOOL_CALL,
        content_key='{"status":"running"}',
        append=True,
        accumulate_content=False,
    ) == (True, True)


def test_extract_tool_part_payload_normalizes_structured_state() -> None:
    assert _extract_tool_part_payload(
        {
            "callID": " call-1 ",
            "tool": " bash ",
            "state": {
                "status": " running ",
                "title": "Execute command",
                "subtitle": "phase 1",
                "input": {"cmd": "pwd"},
                "output": {"stdout": "/workspace"},
                "error": None,
            },
        }
    ) == {
        "call_id": "call-1",
        "tool": "bash",
        "status": "running",
        "title": "Execute command",
        "subtitle": "phase 1",
        "input": {"cmd": "pwd"},
        "output": {"stdout": "/workspace"},
    }
    assert _extract_tool_part_payload({"callID": " ", "tool": None, "state": {}}) is None


def test_extract_token_usage_ignores_non_step_finish_part_payload() -> None:
    assert (
        _extract_token_usage(
            {
                "properties": {
                    "part": {
                        "type": "tool",
                        "tokens": {"input": 9, "output": 3, "total": 12},
                        "cost": 0.4,
                    }
                }
            }
        )
        is None
    )


@pytest.mark.asyncio
async def test_streaming_filters_user_echo_and_emits_single_artifact_block_types() -> None:
    user_text = "who are you"
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="user", part_type="text", delta=user_text),
            _event(session_id="ses-1", role="assistant", part_type="reasoning", delta="thinking"),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta='{"tool":"search"}',
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="final answer"),
        ],
        response_text="final answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-1", context_id="ctx-1", text=user_text), queue
    )

    updates = _artifact_updates(queue)
    assert updates
    texts = [_part_text(event) for event in updates]
    assert user_text not in texts
    block_types = [_artifact_stream_meta(event)["block_type"] for event in updates]
    assert _unique(block_types) == ["reasoning", "tool_call", "text"]
    artifact_ids = [event.artifact.artifact_id for event in updates]
    assert len(set(artifact_ids)) == 1
    event_ids = [_artifact_stream_meta(event)["event_id"] for event in updates]
    assert event_ids == [f"task-1:ctx-1:task-1:stream:{seq}" for seq in range(1, len(updates) + 1)]


@pytest.mark.asyncio
async def test_streaming_waits_for_session_idle_before_emitting_completed() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="late final answer",
            ),
        ],
        response_text="",
        send_delay=0,
        stream_event_delays=[0.05],
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-late", context_id="ctx-late", text="hello"), queue
    )

    updates = _artifact_updates(queue)
    assert updates
    assert _part_text(updates[-1]) == "late final answer"

    final_statuses = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ]
    assert final_statuses
    assert final_statuses[-1].status.state == TaskState.completed


@pytest.mark.asyncio
async def test_streaming_fails_when_event_stream_ends_before_terminal_signal() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="partial answer",
            ),
        ],
        response_text="",
        send_delay=0,
        auto_idle=False,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-no-terminal", context_id="ctx-no-terminal", text="hello"
        ),
        queue,
    )

    final_statuses = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ]
    assert final_statuses
    assert final_statuses[-1].status.state == TaskState.failed
    assert final_statuses[-1].metadata is not None
    assert final_statuses[-1].metadata["opencode"]["error"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


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

    await executor.execute(
        make_request_context(task_id="task-2", context_id="ctx-2", text="hi"), queue
    )

    final_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert len(final_updates) == 1
    assert _part_text(final_updates[0]) == "stable final answer"
    assert _artifact_stream_meta(final_updates[0])["source"] != "final_snapshot"


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

    await executor.execute(
        make_request_context(task_id="task-3", context_id="ctx-3", text="hello"), queue
    )

    final_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert len(final_updates) == 1
    final_event = final_updates[0]
    assert _part_text(final_event) == "final answer from send_message"
    assert _artifact_stream_meta(final_event)["source"] == "final_snapshot"
    assert final_event.append is True
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
    metadata = {"shared": {"session": {"id": "ses-shared"}}}

    await asyncio.gather(
        executor.execute(
            make_request_context(
                task_id="task-4", context_id="ctx-4", text="hello", metadata=metadata
            ),
            queue_1,
        ),
        executor.execute(
            make_request_context(
                task_id="task-5", context_id="ctx-5", text="world", metadata=metadata
            ),
            queue_2,
        ),
    )

    assert client.max_in_flight_send == 1


@pytest.mark.asyncio
async def test_streaming_emits_events_without_message_id_using_stable_fallback() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="final answer from send_message",
                message_id=None,
            ),
        ],
        response_text="final answer from send_message",
        response_message_id=None,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-6", context_id="ctx-6", text="hello"), queue
    )

    updates = _artifact_updates(queue)
    assert len(updates) == 1
    update = updates[0]
    assert _part_text(update) == "final answer from send_message"
    assert _artifact_stream_meta(update)["source"] == "delta"
    assert _artifact_stream_meta(update)["block_type"] == "text"
    assert _artifact_stream_meta(update)["message_id"] == "task-6:ctx-6:assistant"
    assert _artifact_stream_meta(update)["event_id"] == "task-6:ctx-6:task-6:stream:1"
    assert _artifact_stream_meta(update)["sequence"] == 1
    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    assert _status_shared_meta(final_status)["stream"]["message_id"] == "task-6:ctx-6:assistant"
    assert (
        _status_shared_meta(final_status)["stream"]["event_id"]
        == "task-6:ctx-6:task-6:stream:status"
    )


@pytest.mark.asyncio
async def test_streaming_emits_snapshot_when_message_id_missing_and_stream_is_partial() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                delta="partial ",
                message_id=None,
            ),
        ],
        response_text="partial final answer",
        response_message_id=None,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-6b", context_id="ctx-6b", text="hello"), queue
    )

    updates = _artifact_updates(queue)
    assert len(updates) == 2
    first, second = updates

    assert _part_text(first) == "partial "
    assert first.append is False
    assert first.last_chunk is None
    assert _artifact_stream_meta(first)["source"] == "delta"
    assert _artifact_stream_meta(first)["message_id"] == "task-6b:ctx-6b:assistant"
    assert _artifact_stream_meta(first)["event_id"] == "task-6b:ctx-6b:task-6b:stream:1"
    assert _artifact_stream_meta(first)["sequence"] == 1

    assert _part_text(second) == "partial final answer"
    assert second.append is True
    assert second.last_chunk is True
    assert _artifact_stream_meta(second)["source"] == "final_snapshot"
    assert _artifact_stream_meta(second)["message_id"] == "task-6b:ctx-6b:assistant"
    assert _artifact_stream_meta(second)["event_id"] == "task-6b:ctx-6b:task-6b:stream:2"
    assert _artifact_stream_meta(second)["sequence"] == 2

    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    assert _status_shared_meta(final_status)["stream"]["message_id"] == "task-6b:ctx-6b:assistant"
    assert (
        _status_shared_meta(final_status)["stream"]["event_id"]
        == "task-6b:ctx-6b:task-6b:stream:status"
    )


@pytest.mark.asyncio
async def test_streaming_includes_usage_in_final_status_metadata() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
            _step_finish_usage_event(
                session_id="ses-1",
                input_tokens=12,
                output_tokens=4,
                total_tokens=16,
                cost=0.0012,
            ),
        ],
        response_text="answer",
        response_raw={
            "info": {
                "tokens": {
                    "input": 11,
                    "output": 5,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
                "cost": 0.0009,
            }
        },
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-usage", context_id="ctx-usage", text="hello"),
        queue,
    )

    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    usage = _status_shared_meta(final_status)["usage"]
    assert usage["input_tokens"] == 12
    assert usage["output_tokens"] == 4
    assert usage["total_tokens"] == 16
    assert usage["cost"] == 0.0012
    assert "raw" not in usage
    assert final_status.status.state == TaskState.completed


@pytest.mark.asyncio
async def test_streaming_ignores_non_step_finish_usage_like_part_payloads() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-usage-lookalike",
                part_overrides={
                    "callID": "call-usage-lookalike",
                    "tool": "bash",
                    "tokens": {"input": 99, "output": 1, "total": 100},
                    "cost": 1.2,
                    "state": {"status": "running"},
                },
            ),
        ],
        response_text="answer",
        response_raw={
            "info": {
                "tokens": {
                    "input": 11,
                    "output": 5,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
                "cost": 0.0009,
            }
        },
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-usage-guard",
            context_id="ctx-usage-guard",
            text="hello",
        ),
        queue,
    )

    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    usage = _status_shared_meta(final_status)["usage"]
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 5
    assert usage["cost"] == 0.0009
    tool_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "tool_call"
    ]
    assert len(tool_updates) == 1


@pytest.mark.asyncio
async def test_streaming_final_status_state_is_completed() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-final-stream", context_id="ctx-final-stream", text="hello"
        ),
        queue,
    )

    final_statuses = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ]
    assert final_statuses
    assert final_statuses[-1].status.state == TaskState.completed


@pytest.mark.asyncio
async def test_streaming_emits_text_from_step_finish_snapshot_part() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-step-finish-snapshot",
                        "sessionID": "ses-1",
                        "messageID": "msg-1",
                        "type": "step-finish",
                        "reason": "stop",
                        "snapshot": "final answer from snapshot",
                        "cost": 0.0,
                        "tokens": {
                            "input": 1,
                            "output": 4,
                            "reasoning": 0,
                            "cache": {"read": 0, "write": 0},
                        },
                    }
                },
            }
        ],
        response_text="",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-step-finish", context_id="ctx-step-finish", text="hello"
        ),
        queue,
    )

    updates = _artifact_updates(queue)
    assert updates
    text_updates = [
        event for event in updates if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert text_updates
    assert _part_text(text_updates[-1]) == "final answer from snapshot"

    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    usage = _status_shared_meta(final_status)["usage"]
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == 4


@pytest.mark.asyncio
async def test_non_streaming_response_task_state_is_completed() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-final-non-stream", context_id="ctx-final-non-stream", text="hello"
        ),
        queue,
    )

    tasks = [event for event in queue.events if isinstance(event, Task)]
    assert tasks
    assert tasks[-1].status.state == TaskState.completed


@pytest.mark.asyncio
async def test_streaming_emits_interrupt_status_for_permission_asked_event() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _permission_asked_event(session_id="ses-1", request_id="perm-req-1"),
            _permission_asked_event(session_id="ses-1", request_id="perm-req-1"),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-perm", context_id="ctx-perm", text="hello"),
        queue,
    )

    interrupt_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt", {}).get("type")
        == "permission"
    ]
    assert len(interrupt_statuses) == 1
    interrupt = _interrupt_meta(interrupt_statuses[0])
    assert interrupt["request_id"] == "perm-req-1"
    assert interrupt["phase"] == "asked"
    assert interrupt["details"]["permission"] == "read"
    assert "/data/project/.env.secret" in interrupt["details"]["patterns"]
    assert "metadata" not in interrupt["details"]
    assert "tool" not in interrupt["details"]
    assert interrupt_statuses[0].status.state == TaskState.input_required


@pytest.mark.asyncio
async def test_streaming_emits_interrupt_status_for_question_asked_event() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _question_asked_event(session_id="ses-1", request_id="q-req-1"),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-question", context_id="ctx-question", text="hello"),
        queue,
    )

    interrupt_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt", {}).get("type") == "question"
    ]
    assert len(interrupt_statuses) == 1
    interrupt = _interrupt_meta(interrupt_statuses[0])
    assert interrupt["request_id"] == "q-req-1"
    assert interrupt["phase"] == "asked"
    assert interrupt["details"]["questions"] == [
        {
            "header": "Confirm",
            "question": "Proceed?",
            "options": [{"label": "Yes", "value": "yes"}],
        }
    ]
    assert "tool" not in interrupt["details"]
    assert interrupt_statuses[0].status.state == TaskState.input_required


@pytest.mark.asyncio
async def test_streaming_normalizes_question_interrupt_details() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "question.asked",
                "properties": {
                    "id": "q-req-rich",
                    "sessionID": "ses-1",
                    "display_message": "Please confirm how the agent should continue.",
                    "description": "This should stay out of shared interrupt details.",
                    "questions": [
                        {
                            "header": " Confirm ",
                            "question": " Proceed? ",
                            "ignored": "drop-me",
                            "options": [
                                {
                                    "label": " Yes ",
                                    "value": " yes ",
                                    "description": " continue ",
                                    "extra": "drop-me",
                                },
                                {
                                    "label": " ",
                                    "value": "no",
                                },
                                "invalid",
                            ],
                        },
                        {
                            "ignored": "only-invalid",
                            "options": [{"extra": "drop-me"}],
                        },
                    ],
                },
            },
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-question-normalized",
            context_id="ctx-question-normalized",
            text="hello",
        ),
        queue,
    )

    interrupt_status = next(
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt", {}).get("type") == "question"
    )
    interrupt = _interrupt_meta(interrupt_status)
    assert interrupt["details"]["questions"] == [
        {
            "header": "Confirm",
            "question": "Proceed?",
            "options": [
                {
                    "label": "Yes",
                    "value": "yes",
                    "description": "continue",
                },
                {
                    "value": "no",
                },
            ],
        }
    ]
    assert "display_message" not in interrupt["details"]
    assert "description" not in interrupt["details"]


@pytest.mark.asyncio
async def test_streaming_resolved_interrupt_only_clears_internal_pending_state() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _permission_asked_event(session_id="ses-1", request_id="perm-req-resolve"),
            _interrupt_resolved_event(
                session_id="ses-1",
                request_id="perm-req-resolve",
                event_type="permission.replied",
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-interrupt-resolved",
            context_id="ctx-interrupt-resolved",
            text="hello",
        ),
        queue,
    )

    interrupt_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt") is not None
    ]
    assert len(interrupt_statuses) == 2
    asked_interrupt = _interrupt_meta(interrupt_statuses[0])
    resolved_interrupt = _interrupt_meta(interrupt_statuses[1])
    assert asked_interrupt["request_id"] == "perm-req-resolve"
    assert asked_interrupt["phase"] == "asked"
    assert interrupt_statuses[0].status.state == TaskState.input_required
    assert resolved_interrupt["request_id"] == "perm-req-resolve"
    assert resolved_interrupt["type"] == "permission"
    assert resolved_interrupt["phase"] == "resolved"
    assert resolved_interrupt["resolution"] == "replied"
    assert "details" not in resolved_interrupt
    assert interrupt_statuses[1].status.state == TaskState.working
    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    assert "interrupt" not in (final_status.metadata or {}).get("shared", {})


@pytest.mark.asyncio
async def test_streaming_duplicate_interrupt_resolved_event_is_not_emitted_twice() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _question_asked_event(session_id="ses-1", request_id="q-req-resolve"),
            _interrupt_resolved_event(
                session_id="ses-1",
                request_id="q-req-resolve",
                event_type="question.rejected",
            ),
            _interrupt_resolved_event(
                session_id="ses-1",
                request_id="q-req-resolve",
                event_type="question.rejected",
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="answer"),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-interrupt-resolved-dedupe",
            context_id="ctx-interrupt-resolved-dedupe",
            text="hello",
        ),
        queue,
    )

    interrupt_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and event.final is False
        and (event.metadata or {}).get("shared", {}).get("interrupt") is not None
    ]
    assert len(interrupt_statuses) == 2
    assert _interrupt_meta(interrupt_statuses[0])["phase"] == "asked"
    resolved_interrupt = _interrupt_meta(interrupt_statuses[1])
    assert resolved_interrupt["phase"] == "resolved"
    assert resolved_interrupt["type"] == "question"
    assert resolved_interrupt["resolution"] == "rejected"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


@pytest.mark.asyncio
async def test_streaming_treats_embedded_markers_as_plain_text_without_typed_parts() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="text", delta="start "),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="<thin"),
            _event(
                session_id="ses-1", role="assistant", part_type="text", delta="k>thinking</think> "
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="middle "),
            _event(
                session_id="ses-1", role="assistant", part_type="text", delta='[tool_call: {"foo":'
            ),
            _event(session_id="ses-1", role="assistant", part_type="text", delta="1}] end"),
        ],
        response_text='start <think>thinking</think> middle [tool_call: {"foo":1}] end',
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-embedded", context_id="ctx-embedded", text="go"), queue
    )

    updates = _artifact_updates(queue)

    def _final_state(block_type: str) -> str:
        parts = []
        for ev in updates:
            if _artifact_stream_meta(ev)["block_type"] == block_type:
                if not ev.append:
                    parts = [_part_text(ev)]
                else:
                    parts.append(_part_text(ev))
        return "".join(parts)

    assert _final_state("text") == 'start <think>thinking</think> middle [tool_call: {"foo":1}] end'
    assert _final_state("reasoning") == ""
    assert _final_state("tool_call") == ""


@pytest.mark.asyncio
async def test_streaming_emits_structured_tool_part_updates() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-1",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "pending"},
                },
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-1",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "running"},
                },
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-1",
                part_overrides={
                    "callID": "call-1",
                    "tool": "bash",
                    "state": {"status": "completed"},
                },
            ),
        ],
        response_text="done",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-tool-bracket", context_id="ctx-tool-bracket", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    tool_updates = [ev for ev in updates if _artifact_stream_meta(ev)["block_type"] == "tool_call"]
    assert len(tool_updates) == 3
    payloads = [_part_data(ev) for ev in tool_updates]
    assert [payload["status"] for payload in payloads] == ["pending", "running", "completed"]
    assert all(payload["call_id"] == "call-1" for payload in payloads)
    assert all(payload["tool"] == "bash" for payload in payloads)
    assert all(getattr(ev.artifact.parts[0].root, "kind", None) == "data" for ev in tool_updates)


@pytest.mark.asyncio
async def test_streaming_flushes_partial_marker_on_eof_as_current_block_type() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="text", delta="hello <thin"),
        ],
        response_text="hello <thin",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-eof-flush", context_id="ctx-eof-flush", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    assert updates
    assert "".join(_part_text(ev) for ev in updates) == "hello <thin"
    assert all(_artifact_stream_meta(ev)["block_type"] == "text" for ev in updates)


@pytest.mark.asyncio
async def test_streaming_never_resets_single_artifact_after_first_chunk() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-no-reset-1",
                        "sessionID": "ses-1",
                        "type": "text",
                        "role": "assistant",
                        "messageID": "msg-1",
                        "text": "hello",
                    },
                    "delta": "",
                },
            },
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-no-reset-1",
                        "sessionID": "ses-1",
                        "type": "text",
                        "role": "assistant",
                        "messageID": "msg-1",
                        "text": "HELLO",
                    },
                    "delta": "",
                },
            },
        ],
        response_text="HELLO",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-no-reset", context_id="ctx-no-reset", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    assert len(updates) >= 2
    assert updates[0].append is False
    assert all(ev.append is True for ev in updates[1:])


@pytest.mark.asyncio
async def test_streaming_suppresses_reasoning_snapshot_reset_after_delta() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-r1",
                text="",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="reasoning line\n\n",
                part_id="prt-r1",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-r1",
                text="reasoning line",
            ),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-reason-reset", context_id="ctx-reason-reset", text="go"),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert len(reasoning_updates) == 1
    assert _part_text(reasoning_updates[0]) == "reasoning line\n\n"


@pytest.mark.asyncio
async def test_streaming_supports_message_part_delta_events() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-r2",
                text="",
            ),
            _delta_event(session_id="ses-1", part_id="prt-r2", delta="first "),
            _delta_event(session_id="ses-1", part_id="prt-r2", delta="second"),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-delta", context_id="ctx-delta", text="go"),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert reasoning_updates
    merged = "".join(_part_text(ev) for ev in reasoning_updates)
    assert merged == "first second"


@pytest.mark.asyncio
async def test_streaming_buffers_delta_until_part_updated_arrives() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _delta_event(session_id="ses-1", part_id="prt-late", delta="first "),
            _delta_event(session_id="ses-1", part_id="prt-late", delta="second"),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="",
                part_id="prt-late",
                text="first second",
            ),
        ],
        response_text="answer",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-buffered-delta", context_id="ctx-buffered-delta", text="go"
        ),
        queue,
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert reasoning_updates
    merged = "".join(_part_text(ev) for ev in reasoning_updates)
    assert merged == "first second"


@pytest.mark.asyncio
async def test_streaming_logs_raw_upstream_events_at_debug(caplog) -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-debug",
                part_overrides={
                    "callID": "call-debug",
                    "tool": "bash",
                    "state": {"status": "running"},
                    "text": "x" * 80,
                },
            )
        ],
        response_text="done",
    )
    client.settings = make_settings(
        a2a_bearer_token="test",
        opencode_base_url="http://localhost",
        a2a_log_body_limit=64,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.agent"):
        await executor.execute(
            make_request_context(task_id="task-debug", context_id="ctx-debug", text="go"),
            queue,
        )

    messages = [record.message for record in caplog.records]
    assert any("OpenCode stream event type=message.part.updated" in message for message in messages)
    assert any("part_type=tool" in message for message in messages)
    assert any("part_id=prt-tool-debug" in message for message in messages)
    assert any("payload=" in message and "[truncated]" in message for message in messages)


@pytest.mark.asyncio
async def test_streaming_logs_interrupt_payload_at_debug_with_redaction(caplog) -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-debug-1",
                    "sessionID": "ses-1",
                    "permission": "bash",
                    "patterns": ["git push origin main"],
                    "request": {
                        "description": "Agent wants to push the branch.",
                    },
                    "context": {
                        "reason": "The fix is ready for review.",
                    },
                    "prompt": {
                        "message": "Allow the agent to push?",
                    },
                    "accessToken": "super-secret-token",
                },
            }
        ],
        response_text="done",
    )
    client.settings = make_settings(
        a2a_bearer_token="test",
        opencode_base_url="http://localhost",
        a2a_log_body_limit=0,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.agent"):
        await executor.execute(
            make_request_context(
                task_id="task-interrupt-debug", context_id="ctx-interrupt-debug", text="go"
            ),
            queue,
        )

    messages = [record.message for record in caplog.records]
    assert any("OpenCode stream event type=permission.asked" in message for message in messages)
    assert any(
        '"request":{"description":"Agent wants to push the branch."}' in message
        for message in messages
    )
    assert any(
        '"context":{"reason":"The fix is ready for review."}' in message for message in messages
    )
    assert any('"prompt":{"message":"Allow the agent to push?"}' in message for message in messages)
    assert any('"accessToken":"[redacted]"' in message for message in messages)
    assert not any("super-secret-token" in message for message in messages)


@pytest.mark.asyncio
async def test_streaming_does_not_log_raw_upstream_events_above_debug(caplog) -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="thinking",
                part_id="prt-no-debug",
            )
        ],
        response_text="done",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    with caplog.at_level(logging.INFO, logger="opencode_a2a_server.agent"):
        await executor.execute(
            make_request_context(task_id="task-no-debug", context_id="ctx-no-debug", text="go"),
            queue,
        )

    assert not any("OpenCode stream event type=" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_streaming_keeps_multiple_message_ids_in_same_request_window() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                part_id="prt-m1",
                message_id="msg-a",
                delta="step one ",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                part_id="prt-m2",
                message_id="msg-b",
                delta="final answer",
            ),
        ],
        response_text="final answer",
        response_message_id="msg-b",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-multi-mid", context_id="ctx-multi-mid", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    message_ids = [_artifact_stream_meta(ev).get("message_id") for ev in updates]
    assert "msg-a" in message_ids
    assert "msg-b" in message_ids

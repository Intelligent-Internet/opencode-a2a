import pytest

from opencode_a2a_server.agent import (
    OpencodeAgentExecutor,
)
from tests.helpers import (
    DummyEventQueue,
    make_request_context,
)
from tests.streaming_output_contract_support import (
    DummyStreamingClient,
    _artifact_stream_meta,
    _artifact_updates,
    _delta_event,
    _event,
    _part_data,
    _part_text,
)


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
    assert {_artifact_stream_meta(ev)["source"] for ev in reasoning_updates} == {"stream"}


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
    assert {_artifact_stream_meta(ev)["source"] for ev in reasoning_updates} == {"stream"}

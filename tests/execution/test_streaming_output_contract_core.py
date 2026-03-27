import asyncio
from types import SimpleNamespace

import pytest
from a2a.types import (
    FilePart,
    FileWithUri,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
)

from opencode_a2a.execution.executor import (
    BlockType,
    OpencodeAgentExecutor,
    _extract_token_usage,
    _extract_tool_part_payload,
    _StreamOutputState,
)
from tests.support.helpers import (
    DummyEventQueue,
    make_request_context,
    make_request_context_with_parts,
)
from tests.support.streaming_output import (
    DummyStreamingClient,
    _artifact_stream_meta,
    _artifact_updates,
    _event,
    _part_text,
    _progress_meta,
    _status_shared_meta,
    _step_finish_usage_event,
    _unique,
)


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


def test_extract_tool_part_payload_preserves_task_tool_subtask_fields() -> None:
    assert _extract_tool_part_payload(
        {
            "callID": "call-task-1",
            "tool": "task",
            "state": {
                "status": "completed",
                "input": {
                    "prompt": "search auth diffs",
                    "description": "inspect auth changes",
                    "subagent_type": "general",
                    "command": "/review",
                },
                "output": {"summary": "done"},
            },
        }
    ) == {
        "call_id": "call-task-1",
        "tool": "task",
        "status": "completed",
        "input": {
            "prompt": "search auth diffs",
            "description": "inspect auth changes",
            "subagent_type": "general",
            "command": "/review",
        },
        "output": {"summary": "done"},
    }


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
async def test_streaming_emits_only_failed_terminal_status_for_session_error() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "session.error",
                "properties": {
                    "sessionID": "ses-1",
                    "error": {
                        "name": "ProviderAuthError",
                        "data": {
                            "statusCode": 401,
                            "message": "bad key",
                        },
                    },
                },
            }
        ],
        response_text="",
        send_delay=0,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(
            task_id="task-session-error", context_id="ctx-session-error", text="hi"
        ),
        queue,
    )

    final_statuses = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ]
    assert len(final_statuses) == 1
    assert final_statuses[0].status.state == TaskState.auth_required
    assert final_statuses[0].metadata is not None
    assert final_statuses[0].metadata["opencode"]["error"]["type"] == "UPSTREAM_UNAUTHORIZED"
    assert not any(event.status.state == TaskState.completed for event in final_statuses)


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
    assert _artifact_stream_meta(final_updates[0])["source"] == "stream"


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
async def test_streaming_final_snapshot_does_not_repeat_reasoning_content() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(session_id="ses-1", role="assistant", part_type="reasoning", delta="draft plan"),
        ],
        response_text="final answer from send_message",
        response_raw={
            "parts": [
                {"type": "reasoning", "text": "draft plan"},
                {"type": "text", "text": "final answer from send_message"},
            ]
        },
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-3b", context_id="ctx-3b", text="hello"), queue
    )

    reasoning_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "reasoning"
    ]
    assert len(reasoning_updates) == 1
    assert _part_text(reasoning_updates[0]) == "draft plan"

    text_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert len(text_updates) == 1
    assert _part_text(text_updates[0]) == "final answer from send_message"
    assert "draft plan" not in _part_text(text_updates[0])
    assert _artifact_stream_meta(text_updates[0])["source"] == "final_snapshot"


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
    assert _artifact_stream_meta(update)["source"] == "stream"
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
    assert _artifact_stream_meta(first)["source"] == "stream"
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
async def test_streaming_does_not_emit_text_from_step_finish_snapshot_part() -> None:
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

    text_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert text_updates == []

    final_status = [
        event for event in queue.events if isinstance(event, TaskStatusUpdateEvent) and event.final
    ][-1]
    usage = _status_shared_meta(final_status)["usage"]
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == 4


@pytest.mark.asyncio
async def test_streaming_emits_progress_metadata_for_step_events() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-step-start-1",
                        "sessionID": "ses-1",
                        "messageID": "msg-1",
                        "type": "step-start",
                        "reason": "run",
                        "state": {
                            "status": "running",
                            "title": "Planning",
                            "subtitle": "Inspecting repository",
                        },
                    }
                },
            },
            _step_finish_usage_event(
                session_id="ses-1",
                message_id="msg-1",
                part_id="prt-step-finish-1",
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
                cost=0.0002,
            ),
        ],
        response_text="done",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-progress", context_id="ctx-progress", text="hello"),
        queue,
    )

    progress_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and not event.final
        and (event.metadata or {}).get("shared", {}).get("progress") is not None
    ]
    assert len(progress_statuses) == 2

    first = _progress_meta(progress_statuses[0])
    assert first["type"] == "step-start"
    assert first["part_id"] == "prt-step-start-1"
    assert first["reason"] == "run"
    assert first["status"] == "running"
    assert first["title"] == "Planning"
    assert first["subtitle"] == "Inspecting repository"
    assert _status_shared_meta(progress_statuses[0])["stream"]["source"] == "progress"

    second = _progress_meta(progress_statuses[1])
    assert second["type"] == "step-finish"
    assert second["part_id"] == "prt-step-finish-1"
    assert second["reason"] == "stop"


@pytest.mark.asyncio
async def test_streaming_emits_progress_metadata_for_snapshot_without_text_artifact() -> None:
    snapshot_hash = "29ad5b502ac5884b0476ad858a4ebfb5d06c9d21"  # pragma: allowlist secret
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "id": "prt-snapshot-1",
                        "sessionID": "ses-1",
                        "messageID": "msg-1",
                        "type": "snapshot",
                        "snapshot": snapshot_hash,
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
            task_id="task-snapshot-progress", context_id="ctx-snapshot-progress", text="hello"
        ),
        queue,
    )

    progress_statuses = [
        event
        for event in queue.events
        if isinstance(event, TaskStatusUpdateEvent)
        and not event.final
        and (event.metadata or {}).get("shared", {}).get("progress") is not None
    ]
    assert len(progress_statuses) == 1
    progress = _progress_meta(progress_statuses[0])
    assert progress == {"type": "snapshot", "part_id": "prt-snapshot-1"}
    text_updates = [
        event
        for event in _artifact_updates(queue)
        if _artifact_stream_meta(event)["block_type"] == "text"
    ]
    assert text_updates == []


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

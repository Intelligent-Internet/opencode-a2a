import pytest
from a2a.types import (
    TaskState,
    TaskStatusUpdateEvent,
)

from opencode_a2a_server.agent import (
    OpencodeAgentExecutor,
)
from tests.helpers import (
    DummyEventQueue,
    make_request_context,
)
from tests.streaming_output_contract_support import (
    DummyStreamingClient,
    _event,
    _interrupt_meta,
    _interrupt_resolved_event,
    _permission_asked_event,
    _question_asked_event,
)


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

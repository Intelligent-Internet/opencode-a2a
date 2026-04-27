from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.events import EventConsumer, EventQueue
from a2a.server.tasks import TaskManager
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    Artifact,
    Message,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from opencode_a2a.a2a_utils import make_data_part, make_text_part, part_text
from opencode_a2a.output_modes import (
    NegotiatingResultAggregator,
    apply_accepted_output_modes,
    build_output_negotiation_metadata,
    extract_accepted_output_modes_from_metadata,
    normalize_accepted_output_modes,
    part_text_fallback,
)
from opencode_a2a.server.application import OpencodeRequestHandler


def _store() -> InMemoryTaskStore:
    return InMemoryTaskStore(owner_resolver=lambda _context: "test-owner")


def _message(*, message_id: str, text: str, task_id: str, context_id: str) -> Message:
    return Message(
        message_id=message_id,
        role=Role.ROLE_AGENT,
        parts=[make_text_part(text)],
        task_id=task_id,
        context_id=context_id,
    )


def _task_with_negotiated_outputs(*, task_id: str, context_id: str) -> Task:
    metadata = build_output_negotiation_metadata(["text/plain"])
    assert metadata is not None
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            message=_message(
                message_id=f"{task_id}:status",
                text="done",
                task_id=task_id,
                context_id=context_id,
            ),
        ),
        history=[
            _message(
                message_id=f"{task_id}:history",
                text="history",
                task_id=task_id,
                context_id=context_id,
            )
        ],
        artifacts=[
            Artifact(
                artifact_id=f"{task_id}:text",
                parts=[make_text_part("plain result")],
            ),
            Artifact(
                artifact_id=f"{task_id}:json",
                parts=[make_data_part({"tool": "bash", "status": "completed"})],
            ),
        ],
        metadata=metadata,
    )


def test_normalize_accepted_output_modes_treats_wildcards_as_unrestricted() -> None:
    assert normalize_accepted_output_modes(["text/plain", "APPLICATION/JSON"]) == (
        "text/plain",
        "application/json",
    )
    assert normalize_accepted_output_modes(["text/plain", "*/*"]) is None
    assert normalize_accepted_output_modes(["*"]) is None


def test_part_text_fallback_serializes_data_parts_as_stable_json() -> None:
    assert part_text_fallback(make_data_part({"tool": "bash", "status": "running"})) == (
        '{"status":"running","tool":"bash"}'
    )


def test_apply_accepted_output_modes_downgrades_task_data_parts_to_text() -> None:
    task = Task(
        id="task-send",
        context_id="ctx-send",
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            message=Message(
                message_id="msg-send",
                role=Role.ROLE_AGENT,
                parts=[make_data_part({"tool": "bash", "status": "running"})],
                task_id="task-send",
                context_id="ctx-send",
            ),
        ),
        artifacts=[
            Artifact(
                artifact_id="artifact-send",
                parts=[make_data_part({"tool": "bash", "status": "running"})],
            )
        ],
    )

    downgraded = apply_accepted_output_modes(task, ["text/plain"])

    assert isinstance(downgraded, Task)
    assert downgraded.status.message is not None
    assert part_text(downgraded.status.message.parts[0]) == '{"status":"running","tool":"bash"}'
    assert downgraded.artifacts is not None
    assert part_text(downgraded.artifacts[0].parts[0]) == '{"status":"running","tool":"bash"}'


@pytest.mark.asyncio
async def test_negotiating_result_aggregator_persists_metadata_for_artifact_first_flow() -> None:
    store = _store()
    task_manager = TaskManager(
        context=ServerCallContext(),
        task_id="task-artifact-first",
        context_id="ctx-artifact-first",
        task_store=store,
        initial_message=None,
    )
    aggregator = NegotiatingResultAggregator(task_manager, ["text/plain"])
    queue = EventQueue()

    await queue.enqueue_event(
        TaskArtifactUpdateEvent(
            task_id="task-artifact-first",
            context_id="ctx-artifact-first",
            artifact=Artifact(
                artifact_id="artifact-1",
                parts=[make_text_part("hello")],
            ),
            append=False,
            last_chunk=False,
        )
    )
    await queue.enqueue_event(
        TaskStatusUpdateEvent(
            task_id="task-artifact-first",
            context_id="ctx-artifact-first",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
    )

    result, interrupted, bg_task = await aggregator.consume_and_break_on_interrupt(
        EventConsumer(queue),
        blocking=False,
    )

    assert interrupted is True
    assert isinstance(result, Task)
    assert bg_task is not None
    assert extract_accepted_output_modes_from_metadata(result.metadata) == ("text/plain",)
    assert result.artifacts is not None
    assert [artifact.artifact_id for artifact in result.artifacts] == ["artifact-1"]

    await bg_task
    stored = await store.get("task-artifact-first", ServerCallContext())
    assert stored is not None
    assert extract_accepted_output_modes_from_metadata(stored.metadata) == ("text/plain",)


@pytest.mark.asyncio
async def test_on_get_task_applies_persisted_output_negotiation() -> None:
    store = _store()
    task = _task_with_negotiated_outputs(task_id="task-get", context_id="ctx-get")
    await store.save(task, ServerCallContext())
    handler = OpencodeRequestHandler(agent_executor=AsyncMock(), task_store=store)

    result = await handler.on_get_task(TaskQueryParams(id="task-get"))

    assert result is not None
    assert extract_accepted_output_modes_from_metadata(result.metadata) == ("text/plain",)
    assert result.artifacts is not None
    assert [artifact.artifact_id for artifact in result.artifacts] == [
        "task-get:text",
        "task-get:json",
    ]
    assert part_text(result.artifacts[1].parts[0]) == '{"status":"completed","tool":"bash"}'


@pytest.mark.asyncio
async def test_resubscribe_terminal_task_applies_persisted_output_negotiation() -> None:
    store = _store()
    task = _task_with_negotiated_outputs(task_id="task-resub", context_id="ctx-resub")
    await store.save(task, ServerCallContext())
    handler = OpencodeRequestHandler(agent_executor=AsyncMock(), task_store=store)

    events = []
    async for event in handler.on_resubscribe_to_task(TaskIdParams(id="task-resub")):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], Task)
    assert events[0].artifacts is not None
    assert [artifact.artifact_id for artifact in events[0].artifacts] == [
        "task-resub:text",
        "task-resub:json",
    ]
    assert part_text(events[0].artifacts[1].parts[0]) == '{"status":"completed","tool":"bash"}'

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from a2a.server.context import ServerCallContext
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
)
from google.protobuf.json_format import MessageToDict

from opencode_a2a.a2a_utils import make_data_part
from opencode_a2a.contracts.extensions import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
)
from opencode_a2a.output_modes import (
    annotate_output_negotiation_metadata,
    apply_accepted_output_modes,
    build_output_negotiation_metadata,
    canonicalize_stream_artifacts,
    extract_accepted_output_modes_from_metadata,
    normalize_accepted_output_modes,
)
from opencode_a2a.server.application import OpencodeRequestHandler


def _store() -> InMemoryTaskStore:
    return InMemoryTaskStore(owner_resolver=lambda _context: "test-owner")


def _agent_card() -> AgentCard:
    return AgentCard(name="opencode-a2a", capabilities=AgentCapabilities(streaming=True))


def _message(*, message_id: str, text: str, task_id: str, context_id: str) -> Message:
    return Message(
        message_id=message_id,
        role=Role.ROLE_AGENT,
        parts=[Part(text=text)],
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
                parts=[Part(text="plain result")],
            ),
            Artifact(
                artifact_id=f"{task_id}:json",
                parts=[make_data_part({"tool": "bash", "status": "completed"})],
            ),
        ],
        metadata=metadata,
    )


def _task_with_extension_metadata(*, task_id: str, context_id: str) -> Task:
    metadata = build_output_negotiation_metadata(["text/plain"])
    assert metadata is not None
    metadata["shared"] = {
        "session": {"id": "ses-1"},
        "interrupt": {"request_id": "irq-1", "type": "permission", "phase": "asked"},
        "model": {"providerID": "openai", "modelID": "gpt-5"},
        "usage": {"input_tokens": 12},
    }
    metadata["opencode"] = {
        **metadata["opencode"],
        "directory": "/workspace",
    }
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
        metadata=metadata,
    )


def test_normalize_accepted_output_modes_treats_wildcards_as_unrestricted() -> None:
    assert normalize_accepted_output_modes(["text/plain", "APPLICATION/JSON"]) == (
        "text/plain",
        "application/json",
    )
    assert normalize_accepted_output_modes(["text/plain", "*/*"]) is None
    assert normalize_accepted_output_modes(["*"]) is None


def test_normalize_accepted_output_modes_reads_attribute_sources_and_rejects_scalars() -> None:
    class _Config:
        acceptedOutputModes = [" TEXT/PLAIN ", "application/json", "text/plain", 3]

    assert normalize_accepted_output_modes(_Config()) == ("text/plain", "application/json")
    assert normalize_accepted_output_modes("text/plain") is None
    assert normalize_accepted_output_modes({"accepted_output_modes": ["text/plain"]}) is None


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
    assert downgraded.status.message.parts[0].HasField("text")
    assert downgraded.status.message.parts[0].text == '{"status":"running","tool":"bash"}'
    assert downgraded.artifacts is not None
    assert downgraded.artifacts[0].parts[0].HasField("text")
    assert downgraded.artifacts[0].parts[0].text == '{"status":"running","tool":"bash"}'


def test_apply_accepted_output_modes_can_drop_artifact_event_and_clear_message_parts() -> None:
    artifact_event = TaskArtifactUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        artifact=Artifact(
            artifact_id="artifact-1",
            parts=[Part(text="hello")],
        ),
        append=False,
        last_chunk=False,
    )
    assert apply_accepted_output_modes(artifact_event, ["application/json"]) is None

    message = Message(
        message_id="msg-1",
        role=Role.ROLE_AGENT,
        parts=[Part(text="hello")],
        task_id="task-1",
        context_id="ctx-1",
    )
    cleared = apply_accepted_output_modes(message, ["application/json"])
    assert isinstance(cleared, Message)
    assert len(cleared.parts) == 0


def test_annotate_output_negotiation_metadata_preserves_existing_event_metadata() -> None:
    status_event = TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        metadata={"shared": {"usage": {"total_tokens": 5}}},
    )
    annotated_status = annotate_output_negotiation_metadata(status_event, ["text/plain"])
    assert extract_accepted_output_modes_from_metadata(annotated_status.metadata) == ("text/plain",)
    assert annotated_status.metadata["shared"]["usage"]["total_tokens"] == 5

    artifact_event = TaskArtifactUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        artifact=Artifact(artifact_id="artifact-1", parts=[Part(text="hello")]),
        append=False,
        last_chunk=False,
        metadata={"shared": {"stream": {"block_type": "text"}}},
    )
    annotated_artifact = annotate_output_negotiation_metadata(artifact_event, ["text/plain"])
    assert extract_accepted_output_modes_from_metadata(annotated_artifact.metadata) == (
        "text/plain",
    )
    assert annotated_artifact.metadata["shared"]["stream"]["block_type"] == "text"


def test_canonicalize_stream_artifacts_compacts_text_and_keeps_latest_tool_call() -> None:
    task = Task(
        id="task-stream",
        context_id="ctx-stream",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        artifacts=[
            Artifact(
                artifact_id="text",
                parts=[Part(text="hello "), Part(text="world")],
                metadata={"shared": {"stream": {"block_type": "text"}}},
            ),
            Artifact(
                artifact_id="tool",
                parts=[
                    make_data_part({"state": "running"}),
                    make_data_part({"state": "completed"}),
                ],
                metadata={"shared": {"stream": {"block_type": "tool_call"}}},
            ),
            Artifact(
                artifact_id="static",
                parts=[Part(text="first"), Part(text="second")],
            ),
        ],
    )

    canonical = canonicalize_stream_artifacts(task)

    assert len(canonical.artifacts[0].parts) == 1
    assert canonical.artifacts[0].parts[0].text == "hello world"
    assert len(canonical.artifacts[1].parts) == 1
    assert MessageToDict(canonical.artifacts[1].parts[0])["data"]["state"] == "completed"
    assert len(canonical.artifacts[2].parts) == 2


@pytest.mark.asyncio
async def test_on_get_task_applies_persisted_output_negotiation() -> None:
    store = _store()
    task = _task_with_negotiated_outputs(task_id="task-get", context_id="ctx-get")
    await store.save(task, ServerCallContext())
    handler = OpencodeRequestHandler(
        agent_executor=AsyncMock(),
        task_store=store,
        agent_card=_agent_card(),
    )

    result = await handler.on_get_task(
        GetTaskRequest(id="task-get"),
        ServerCallContext(),
    )

    assert result is not None
    assert extract_accepted_output_modes_from_metadata(result.metadata) == ("text/plain",)
    assert result.artifacts is not None
    assert [artifact.artifact_id for artifact in result.artifacts] == [
        "task-get:text",
        "task-get:json",
    ]
    assert result.artifacts[1].parts[0].HasField("text")
    assert result.artifacts[1].parts[0].text == '{"status":"completed","tool":"bash"}'


@pytest.mark.asyncio
async def test_subscribe_terminal_task_is_unsupported_before_output_negotiation() -> None:
    store = _store()
    task = _task_with_negotiated_outputs(task_id="task-resub", context_id="ctx-resub")
    await store.save(task, ServerCallContext())
    handler = OpencodeRequestHandler(
        agent_executor=AsyncMock(),
        task_store=store,
        agent_card=_agent_card(),
    )

    with pytest.raises(UnsupportedOperationError):
        async for _event in handler.on_subscribe_to_task(
            SubscribeToTaskRequest(id="task-resub"),
            ServerCallContext(),
        ):
            pass


@pytest.mark.asyncio
async def test_on_get_task_filters_unnegotiated_shared_extension_metadata() -> None:
    store = _store()
    task = _task_with_extension_metadata(task_id="task-filter", context_id="ctx-filter")
    await store.save(task, ServerCallContext())
    handler = OpencodeRequestHandler(
        agent_executor=AsyncMock(),
        task_store=store,
        agent_card=_agent_card(),
    )

    unnegotiated = await handler.on_get_task(
        GetTaskRequest(id="task-filter"), context=ServerCallContext()
    )
    assert unnegotiated is not None
    assert "shared" not in unnegotiated.metadata
    assert unnegotiated.metadata["opencode"]["directory"] == "/workspace"

    negotiated = await handler.on_get_task(
        GetTaskRequest(id="task-filter"),
        context=ServerCallContext(
            requested_extensions={
                SESSION_BINDING_EXTENSION_URI,
                INTERRUPT_CALLBACK_EXTENSION_URI,
                MODEL_SELECTION_EXTENSION_URI,
                STREAMING_EXTENSION_URI,
            }
        ),
    )
    assert negotiated is not None
    assert negotiated.metadata["shared"]["session"]["id"] == "ses-1"
    assert negotiated.metadata["shared"]["interrupt"]["request_id"] == "irq-1"
    assert negotiated.metadata["shared"]["model"]["providerID"] == "openai"
    assert negotiated.metadata["shared"]["usage"]["input_tokens"] == 12

    streaming_only = await handler.on_get_task(
        GetTaskRequest(id="task-filter"),
        context=ServerCallContext(
            requested_extensions={
                STREAMING_EXTENSION_URI,
            }
        ),
    )
    assert streaming_only is not None
    assert "session" not in streaming_only.metadata["shared"]
    assert "interrupt" not in streaming_only.metadata["shared"]
    assert streaming_only.metadata["shared"]["usage"]["input_tokens"] == 12

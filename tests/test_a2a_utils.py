from __future__ import annotations

import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict

from opencode_a2a.a2a_utils import (
    clone_proto,
    make_data_part,
    replace_artifact_event_artifact,
    replace_artifact_parts,
    replace_message_parts,
    replace_status_event_message,
)


def test_make_data_part_supports_nested_structured_payloads() -> None:
    part = make_data_part(
        {
            "tool": "bash",
            "ok": True,
            "count": 2,
            "items": [1, "two", None],
            "nested": {"mode": "safe"},
        },
        metadata={"origin": "test"},
    )

    assert MessageToDict(part.data) == {
        "tool": "bash",
        "ok": True,
        "count": 2.0,
        "items": [1.0, "two", None],
        "nested": {"mode": "safe"},
    }
    assert dict(part.metadata) == {"origin": "test"}


def test_make_data_part_rejects_unsupported_payload_type() -> None:
    with pytest.raises(TypeError, match="Unsupported structured payload type"):
        make_data_part(object())


def test_proto_replacement_helpers_return_updated_copies() -> None:
    original_message = Message(
        message_id="msg-1",
        role=Role.ROLE_AGENT,
        parts=[Part(text="before")],
        task_id="task-1",
        context_id="ctx-1",
    )
    cloned_message = clone_proto(original_message)
    cloned_message.parts[0].text = "after"

    replaced_message = replace_message_parts(original_message, [Part(text="replacement")])
    assert original_message.parts[0].text == "before"
    assert cloned_message.parts[0].text == "after"
    assert replaced_message.parts[0].text == "replacement"

    original_artifact = Artifact(artifact_id="artifact-1", parts=[Part(text="draft")])
    replaced_artifact = replace_artifact_parts(original_artifact, [Part(text="final")])
    assert original_artifact.parts[0].text == "draft"
    assert replaced_artifact.parts[0].text == "final"

    original_status_event = TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING, message=original_message),
    )
    cleared_status_event = replace_status_event_message(original_status_event, None)
    updated_status_event = replace_status_event_message(
        original_status_event,
        replaced_message,
    )
    assert original_status_event.status.HasField("message")
    assert cleared_status_event.status.HasField("message") is False
    assert updated_status_event.status.message.parts[0].text == "replacement"

    original_artifact_event = TaskArtifactUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        artifact=original_artifact,
        append=False,
        last_chunk=False,
    )
    updated_artifact_event = replace_artifact_event_artifact(
        original_artifact_event,
        replaced_artifact,
    )
    assert original_artifact_event.artifact.parts[0].text == "draft"
    assert updated_artifact_event.artifact.parts[0].text == "final"

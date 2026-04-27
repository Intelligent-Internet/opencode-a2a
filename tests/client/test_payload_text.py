from __future__ import annotations

from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
)

from opencode_a2a.client.payload_text import extract_text


def test_extract_text_prefers_stream_artifact_payload() -> None:
    update = TaskArtifactUpdateEvent(
        task_id="remote-task",
        context_id="remote-context",
        artifact=Artifact(
            artifact_id="artifact-1",
            name="response",
            parts=[Part(text="streamed remote text")],
        ),
    )

    assert extract_text(StreamResponse(artifact_update=update)) == "streamed remote text"


def test_extract_text_reads_task_status_message() -> None:
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            message=Message(
                role=Role.ROLE_AGENT,
                message_id="m1",
                parts=[Part(text="status message text")],
            ),
        ),
    )

    assert extract_text(task) == "status message text"


def test_extract_text_reads_nested_mapping_payload() -> None:
    payload = {
        "result": {
            "history": [
                {"parts": [{"text": "mapped nested text"}]},
            ]
        }
    }

    assert extract_text(payload) == "mapped nested text"


def test_extract_text_reads_model_dump_payload() -> None:
    class _Payload:
        def model_dump(self) -> dict[str, object]:
            return {"artifacts": [{"parts": [{"text": "model dump text"}]}]}

    assert extract_text(_Payload()) == "model dump text"


def test_extract_text_reads_direct_string_payload() -> None:
    assert extract_text("  string payload  ") == "string payload"


def test_extract_text_reads_message_and_artifact_attributes() -> None:
    class _ArtifactHolder:
        artifact = {"parts": [{"text": "artifact attribute text"}]}

    class _MessageHolder:
        message = {"parts": [{"text": "message attribute text"}]}

    assert extract_text(_ArtifactHolder()) == "artifact attribute text"
    assert extract_text(_MessageHolder()) == "message attribute text"


def test_extract_text_reads_result_history_and_artifacts_attributes() -> None:
    class _ResultHolder:
        result = {"parts": [{"text": "result attribute text"}]}

    class _HistoryHolder:
        history = [{"parts": [{"text": "history attribute text"}]}]

    class _Artifact:
        parts = [{"text": "artifacts attribute text"}]

    class _ArtifactsHolder:
        artifacts = [_Artifact()]

    assert extract_text(_ResultHolder()) == "result attribute text"
    assert extract_text(_HistoryHolder()) == "history attribute text"
    assert extract_text(_ArtifactsHolder()) == "artifacts attribute text"

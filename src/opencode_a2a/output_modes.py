from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from typing import Any, cast

from a2a.types import (
    Artifact,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage

from .a2a_utils import (
    clone_proto,
    replace_artifact_event_artifact,
    replace_artifact_parts,
    replace_message_parts,
    replace_status_event_message,
)

OUTPUT_NEGOTIATION_METADATA_KEY = "output_negotiation"
OUTPUT_NEGOTIATION_ACCEPTED_OUTPUT_MODES_FIELD = "accepted_output_modes"
_OPENCODE_METADATA_KEY = "opencode"
_APPLICATION_JSON_MEDIA_TYPE = "application/json"
_TEXT_PLAIN_MEDIA_TYPE = "text/plain"


def normalize_accepted_output_modes(source: Any) -> tuple[str, ...] | None:
    if source is None:
        return None
    accepted = getattr(source, "accepted_output_modes", None) or getattr(
        source, "acceptedOutputModes", None
    )
    if accepted is not None:
        source = accepted
    if isinstance(source, str | bytes | bytearray | dict) or not isinstance(source, Iterable):
        return None

    normalized: list[str] = []
    for value in cast(Iterable[str], source):
        if not isinstance(value, str):
            continue
        mode = value.strip().lower()
        if not mode or mode in normalized:
            continue
        if mode in {"*", "*/*"}:
            return None
        normalized.append(mode)
    return tuple(normalized) or None


def accepts_output_mode(
    accepted_output_modes: Collection[str] | None,
    media_type: str,
) -> bool:
    return accepted_output_modes is None or media_type in accepted_output_modes


def build_output_negotiation_metadata(
    accepted_output_modes: Iterable[str] | None,
) -> dict[str, Any] | None:
    normalized = normalize_accepted_output_modes(accepted_output_modes)
    if normalized is None:
        return None
    return {
        _OPENCODE_METADATA_KEY: {
            OUTPUT_NEGOTIATION_METADATA_KEY: {
                OUTPUT_NEGOTIATION_ACCEPTED_OUTPUT_MODES_FIELD: sorted(normalized),
            }
        }
    }


def _normalize_metadata_mapping(metadata: Any) -> dict[str, Any]:
    if isinstance(metadata, ProtoMessage):
        normalized = MessageToDict(metadata)
        return normalized if isinstance(normalized, dict) else {}
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return {}


def merge_output_negotiation_metadata(
    metadata: dict[str, Any] | None,
    accepted_output_modes: Iterable[str] | None,
) -> dict[str, Any] | None:
    negotiation_metadata = build_output_negotiation_metadata(accepted_output_modes)
    if negotiation_metadata is None:
        return metadata

    merged = _normalize_metadata_mapping(metadata)
    opencode_metadata = merged.get(_OPENCODE_METADATA_KEY)
    if not isinstance(opencode_metadata, dict):
        opencode_metadata = {}
    else:
        opencode_metadata = dict(opencode_metadata)

    opencode_metadata[OUTPUT_NEGOTIATION_METADATA_KEY] = dict(
        cast(
            dict[str, Any],
            negotiation_metadata[_OPENCODE_METADATA_KEY][OUTPUT_NEGOTIATION_METADATA_KEY],
        )
    )
    merged[_OPENCODE_METADATA_KEY] = opencode_metadata
    return merged


def extract_accepted_output_modes_from_metadata(
    metadata: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    normalized_metadata = _normalize_metadata_mapping(metadata)
    if not normalized_metadata:
        return None
    opencode_metadata = normalized_metadata.get(_OPENCODE_METADATA_KEY)
    if not isinstance(opencode_metadata, dict):
        return None
    negotiation_metadata = opencode_metadata.get(OUTPUT_NEGOTIATION_METADATA_KEY)
    if not isinstance(negotiation_metadata, dict):
        return None
    accepted_output_modes = negotiation_metadata.get(OUTPUT_NEGOTIATION_ACCEPTED_OUTPUT_MODES_FIELD)
    return normalize_accepted_output_modes(accepted_output_modes)


def annotate_output_negotiation_metadata(
    payload: Any,
    accepted_output_modes: Iterable[str] | None,
) -> Any:
    normalized = normalize_accepted_output_modes(accepted_output_modes)
    if normalized is None:
        return payload

    if isinstance(payload, Task):
        updated = clone_proto(payload)
        updated.ClearField("metadata")
        merged_metadata = merge_output_negotiation_metadata(payload.metadata, normalized)
        if merged_metadata:
            updated.metadata.update(merged_metadata)
        return updated

    if isinstance(payload, TaskStatusUpdateEvent):
        updated = clone_proto(payload)
        updated.ClearField("metadata")
        merged_metadata = merge_output_negotiation_metadata(payload.metadata, normalized)
        if merged_metadata:
            updated.metadata.update(merged_metadata)
        return updated

    if isinstance(payload, TaskArtifactUpdateEvent):
        updated = clone_proto(payload)
        updated.ClearField("metadata")
        merged_metadata = merge_output_negotiation_metadata(payload.metadata, normalized)
        if merged_metadata:
            updated.metadata.update(merged_metadata)
        return updated

    return payload


def apply_accepted_output_modes(
    payload: Any,
    accepted_output_modes: Iterable[str] | None,
) -> Any | None:
    normalized = normalize_accepted_output_modes(accepted_output_modes)
    if normalized is None:
        return payload

    if isinstance(payload, TaskArtifactUpdateEvent):
        artifact = _filter_artifact(payload.artifact, normalized)
        if artifact is None:
            return None
        return replace_artifact_event_artifact(payload, artifact)

    if isinstance(payload, TaskStatusUpdateEvent):
        message = (
            _filter_message(payload.status.message, normalized)
            if payload.status.HasField("message")
            else None
        )
        return replace_status_event_message(payload, message)

    if isinstance(payload, Task):
        return _filter_task(payload, normalized)

    if isinstance(payload, Message):
        filtered = _filter_message(payload, normalized)
        if filtered is not None:
            return filtered
        return replace_message_parts(payload, [])

    return payload


def _extract_artifact_stream_block_type(artifact: Artifact | None) -> str | None:
    if artifact is None:
        return None
    metadata = _normalize_metadata_mapping(artifact.metadata)
    if not metadata:
        return None
    shared = metadata.get("shared")
    if not isinstance(shared, dict):
        return None
    stream = shared.get("stream")
    if not isinstance(stream, dict):
        return None
    block_type = stream.get("block_type")
    return block_type.strip() if isinstance(block_type, str) and block_type.strip() else None


def canonicalize_stream_artifacts(task: Task) -> Task:
    """Compact append-only stream chunks before task persistence."""

    updated = clone_proto(task)
    for artifact in updated.artifacts:
        if not artifact.parts:
            continue
        block_type = _extract_artifact_stream_block_type(artifact)
        if block_type in {"text", "reasoning"} and all(
            part.HasField("text") for part in artifact.parts
        ):
            canonical_part = clone_proto(artifact.parts[0])
            canonical_part.text = "".join(part.text for part in artifact.parts)
            artifact.CopyFrom(replace_artifact_parts(artifact, [canonical_part]))
        elif block_type == "tool_call" and len(artifact.parts) > 1:
            artifact.CopyFrom(replace_artifact_parts(artifact, [clone_proto(artifact.parts[-1])]))
    return updated


def _filter_task(task: Task, accepted_output_modes: Collection[str]) -> Task:
    updated = clone_proto(task)
    if updated.status.HasField("message"):
        filtered_message = _filter_message(updated.status.message, accepted_output_modes)
        if filtered_message is None:
            updated.status.ClearField("message")
        else:
            updated.status.message.CopyFrom(filtered_message)

    filtered_history = [
        filtered
        for filtered in (
            _filter_message(message, accepted_output_modes) for message in task.history
        )
        if filtered is not None
    ]
    del updated.history[:]
    updated.history.extend(filtered_history)

    filtered_artifacts = [
        filtered
        for filtered in (
            _filter_artifact(artifact, accepted_output_modes) for artifact in task.artifacts
        )
        if filtered is not None
    ]
    del updated.artifacts[:]
    updated.artifacts.extend(filtered_artifacts)

    return updated


def _filter_message(
    message: Message,
    accepted_output_modes: Collection[str],
) -> Message | None:
    parts = _filter_parts(message.parts, accepted_output_modes)
    if not parts:
        return None
    return replace_message_parts(message, parts)


def _filter_artifact(
    artifact: Artifact,
    accepted_output_modes: Collection[str],
) -> Artifact | None:
    parts = _filter_parts(artifact.parts, accepted_output_modes)
    if not parts:
        return None
    return replace_artifact_parts(artifact, parts)


def _filter_parts(
    parts: list[Part],
    accepted_output_modes: Collection[str],
) -> list[Part]:
    filtered: list[Part] = []
    for part in parts:
        if part.HasField("text"):
            media_type = _TEXT_PLAIN_MEDIA_TYPE
        elif part.HasField("data"):
            media_type = _APPLICATION_JSON_MEDIA_TYPE
        elif part.HasField("raw") or part.HasField("url"):
            media_type = part.media_type or "application/octet-stream"
        else:
            media_type = None
        if media_type is None or accepts_output_mode(accepted_output_modes, media_type):
            filtered.append(part)
            continue
        if accepts_output_mode(accepted_output_modes, _TEXT_PLAIN_MEDIA_TYPE):
            if part.HasField("text"):
                filtered.append(Part(text=part.text))
                continue
            if part.HasField("data"):
                filtered.append(
                    Part(
                        text=json.dumps(
                            MessageToDict(part.data),
                            ensure_ascii=True,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                )
    return filtered

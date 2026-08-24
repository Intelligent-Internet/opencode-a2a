from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage

from .a2a_utils import clone_proto
from .contracts.extensions import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
)

_STREAMING_SHARED_METADATA_KEYS = ("stream", "progress", "usage")


@dataclass(frozen=True)
class ExtensionRequirement:
    extension_uri: str
    field: str


def requested_extensions_from_call_context(
    call_context: ServerCallContext | None,
) -> frozenset[str]:
    if call_context is None:
        return frozenset()
    return frozenset(value.strip() for value in call_context.requested_extensions if value.strip())


def filter_negotiated_extensions_from_payload(
    payload: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    requested_extensions: Iterable[str],
) -> Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent:
    requested = frozenset(value for value in requested_extensions if value)
    if isinstance(payload, Task):
        updated = clone_proto(payload)
        _set_filtered_metadata(updated, requested)
        if updated.status.HasField("message"):
            _set_filtered_metadata(updated.status.message, requested)
        for history_item in updated.history:
            _set_filtered_metadata(history_item, requested)
        for artifact in updated.artifacts:
            _set_filtered_metadata(artifact, requested)
        return updated
    if isinstance(payload, TaskStatusUpdateEvent):
        updated = clone_proto(payload)
        _set_filtered_metadata(updated, requested)
        if updated.status.HasField("message"):
            _set_filtered_metadata(updated.status.message, requested)
        return updated
    if isinstance(payload, TaskArtifactUpdateEvent):
        updated = clone_proto(payload)
        _set_filtered_metadata(updated.artifact, requested)
        return updated
    return payload


def _set_filtered_metadata(
    proto: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Artifact | Message,
    requested_extensions: frozenset[str],
) -> None:
    metadata = getattr(proto, "metadata", None)
    metadata_dict: dict[str, Any] | None
    if isinstance(metadata, ProtoMessage):
        metadata_dict = MessageToDict(metadata, preserving_proto_field_name=True)
    elif isinstance(metadata, Mapping):
        metadata_dict = dict(metadata)
    else:
        metadata_dict = None
    if not metadata_dict:
        proto.ClearField("metadata")
        return
    filtered_metadata: dict[str, Any] = dict(metadata_dict)
    shared_metadata = filtered_metadata.get("shared")
    if isinstance(shared_metadata, Mapping):
        filtered_shared = dict(shared_metadata)
        if SESSION_BINDING_EXTENSION_URI not in requested_extensions:
            filtered_shared.pop("session", None)
        if MODEL_SELECTION_EXTENSION_URI not in requested_extensions:
            filtered_shared.pop("model", None)
        if STREAMING_EXTENSION_URI not in requested_extensions:
            for key in _STREAMING_SHARED_METADATA_KEYS:
                filtered_shared.pop(key, None)
        if INTERRUPT_CALLBACK_EXTENSION_URI not in requested_extensions:
            filtered_shared.pop("interrupt", None)
        if filtered_shared:
            filtered_metadata["shared"] = filtered_shared
        else:
            filtered_metadata.pop("shared", None)
    proto.ClearField("metadata")
    if filtered_metadata:
        proto.metadata.update(filtered_metadata)

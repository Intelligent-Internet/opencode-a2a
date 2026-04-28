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
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    INTERRUPT_RECOVERY_METHODS,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    SESSION_METHODS,
    STREAMING_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
    WORKSPACE_CONTROL_METHODS,
)
from .metadata_access import extract_namespaced_value

_STREAMING_SHARED_METADATA_KEYS = frozenset({"stream", "progress", "interrupt", "usage"})

JSONRPC_EXTENSION_URI_BY_METHOD: dict[str, str] = {
    **{method: SESSION_MANAGEMENT_EXTENSION_URI for method in SESSION_METHODS.values()},
    **{method: PROVIDER_DISCOVERY_EXTENSION_URI for method in PROVIDER_DISCOVERY_METHODS.values()},
    **{method: INTERRUPT_RECOVERY_EXTENSION_URI for method in INTERRUPT_RECOVERY_METHODS.values()},
    **{method: INTERRUPT_CALLBACK_EXTENSION_URI for method in INTERRUPT_CALLBACK_METHODS.values()},
    **{method: WORKSPACE_CONTROL_EXTENSION_URI for method in WORKSPACE_CONTROL_METHODS.values()},
}


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


def required_extensions_for_send_message_params(
    params: Any,
) -> tuple[ExtensionRequirement, ...]:
    sources: list[dict[str, Any]] = []
    params_metadata = getattr(params, "metadata", None)
    if isinstance(params_metadata, ProtoMessage):
        params_metadata = MessageToDict(params_metadata, preserving_proto_field_name=True)
    elif isinstance(params_metadata, Mapping):
        params_metadata = dict(params_metadata)
    else:
        params_metadata = None
    if params_metadata:
        sources.append(params_metadata)
    message = getattr(params, "message", None)
    message_metadata = getattr(message, "metadata", None)
    if isinstance(message_metadata, ProtoMessage):
        message_metadata = MessageToDict(message_metadata, preserving_proto_field_name=True)
    elif isinstance(message_metadata, Mapping):
        message_metadata = dict(message_metadata)
    else:
        message_metadata = None
    if message_metadata:
        sources.append(message_metadata)

    requirements: list[ExtensionRequirement] = []
    if any(
        extract_namespaced_value(source, namespace="shared", path=("session", "id")) is not None
        for source in sources
    ):
        requirements.append(
            ExtensionRequirement(
                extension_uri=SESSION_BINDING_EXTENSION_URI,
                field="metadata.shared.session.id",
            )
        )
    if any(
        extract_namespaced_value(source, namespace="opencode", path=("directory",)) is not None
        for source in sources
    ):
        requirements.append(
            ExtensionRequirement(
                extension_uri=SESSION_BINDING_EXTENSION_URI,
                field="metadata.opencode.directory",
            )
        )
    if any(
        extract_namespaced_value(source, namespace="opencode", path=("workspace", "id")) is not None
        for source in sources
    ):
        requirements.append(
            ExtensionRequirement(
                extension_uri=SESSION_BINDING_EXTENSION_URI,
                field="metadata.opencode.workspace.id",
            )
        )
    if any(
        extract_namespaced_value(source, namespace="shared", path=("model", "providerID"))
        is not None
        or extract_namespaced_value(source, namespace="shared", path=("model", "modelID"))
        is not None
        for source in sources
    ):
        requirements.append(
            ExtensionRequirement(
                extension_uri=MODEL_SELECTION_EXTENSION_URI,
                field="metadata.shared.model",
            )
        )
    return tuple(requirements)


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
    filtered_metadata = _filter_metadata_dict(metadata_dict, requested_extensions)
    proto.ClearField("metadata")
    if filtered_metadata:
        proto.metadata.update(filtered_metadata)


def _filter_metadata_dict(
    metadata: dict[str, Any] | None,
    requested_extensions: frozenset[str],
) -> dict[str, Any] | None:
    if not metadata:
        return None
    normalized = dict(metadata)
    shared_metadata = normalized.get("shared")
    if isinstance(shared_metadata, Mapping):
        filtered_shared = dict(shared_metadata)
        if (
            SESSION_BINDING_EXTENSION_URI not in requested_extensions
            and STREAMING_EXTENSION_URI not in requested_extensions
        ):
            filtered_shared.pop("session", None)
        if MODEL_SELECTION_EXTENSION_URI not in requested_extensions:
            filtered_shared.pop("model", None)
        if STREAMING_EXTENSION_URI not in requested_extensions:
            for key in _STREAMING_SHARED_METADATA_KEYS:
                filtered_shared.pop(key, None)
        if filtered_shared:
            normalized["shared"] = filtered_shared
        else:
            normalized.pop("shared", None)
    return normalized or None

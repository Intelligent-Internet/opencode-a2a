from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Message, Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from google.protobuf.message import Message as ProtoMessage

from .a2a_utils import clone_proto, proto_to_dict
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


def merge_extension_service_parameters(
    service_parameters: Mapping[str, str] | None,
    extensions: Sequence[str] | None,
) -> dict[str, str] | None:
    normalized_extensions = [
        value for value in list(extensions or []) if isinstance(value, str) and value
    ]
    base = dict(service_parameters or {})
    if not base and not normalized_extensions:
        return None
    updates = [with_a2a_extensions(normalized_extensions)] if normalized_extensions else []
    merged = ServiceParametersFactory.create_from(base or None, updates)
    return merged or None


def requested_extensions_from_headers(headers: Any) -> frozenset[str]:
    values: list[str] = []
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        values.extend(str(value) for value in getlist(HTTP_EXTENSION_HEADER) if value)
    else:
        getter = getattr(headers, "get", None)
        if callable(getter):
            for name in (HTTP_EXTENSION_HEADER, HTTP_EXTENSION_HEADER.lower()):
                value = getter(name)
                if value:
                    values.append(str(value))
    return frozenset(get_requested_extensions(values))


def requested_extensions_from_call_context(
    call_context: ServerCallContext | None,
) -> frozenset[str]:
    if call_context is None:
        return frozenset()
    return frozenset(
        value.strip()
        for value in call_context.requested_extensions
        if isinstance(value, str) and value.strip()
    )


def requested_extensions_from_request_context(context: RequestContext) -> frozenset[str]:
    return requested_extensions_from_call_context(context.call_context)


def missing_requested_extensions(
    requested_extensions: Iterable[str],
    required_extensions: Iterable[str],
) -> tuple[str, ...]:
    requested = {value for value in requested_extensions if isinstance(value, str) and value}
    missing = [
        extension_uri
        for extension_uri in required_extensions
        if isinstance(extension_uri, str) and extension_uri and extension_uri not in requested
    ]
    return tuple(sorted(set(missing)))


def required_extensions_for_send_message_params(
    params: Any,
) -> tuple[ExtensionRequirement, ...]:
    sources = _metadata_sources_for_send_message(params)
    requirements: list[ExtensionRequirement] = []
    if _metadata_field_present(sources, namespace="shared", path=("session", "id")):
        requirements.append(
            ExtensionRequirement(
                extension_uri=SESSION_BINDING_EXTENSION_URI,
                field="metadata.shared.session.id",
            )
        )
    if _metadata_field_present(sources, namespace="opencode", path=("directory",)):
        requirements.append(
            ExtensionRequirement(
                extension_uri=SESSION_BINDING_EXTENSION_URI,
                field="metadata.opencode.directory",
            )
        )
    if _metadata_field_present(sources, namespace="opencode", path=("workspace", "id")):
        requirements.append(
            ExtensionRequirement(
                extension_uri=SESSION_BINDING_EXTENSION_URI,
                field="metadata.opencode.workspace.id",
            )
        )
    if _metadata_field_present(
        sources, namespace="shared", path=("model", "providerID")
    ) or _metadata_field_present(sources, namespace="shared", path=("model", "modelID")):
        requirements.append(
            ExtensionRequirement(
                extension_uri=MODEL_SELECTION_EXTENSION_URI,
                field="metadata.shared.model",
            )
        )
    return tuple(_dedupe_requirements(requirements))


def filter_negotiated_extensions_from_payload(
    payload: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    requested_extensions: Iterable[str],
) -> Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent:
    requested = frozenset(
        value for value in requested_extensions if isinstance(value, str) and value
    )
    if isinstance(payload, Task):
        return _filter_task(payload, requested)
    if isinstance(payload, TaskStatusUpdateEvent):
        return _filter_status_update(payload, requested)
    if isinstance(payload, TaskArtifactUpdateEvent):
        return _filter_artifact_update(payload, requested)
    return payload


def _metadata_sources_for_send_message(
    params: Any,
) -> tuple[Mapping[str, Any] | None, ...]:
    sources: list[Mapping[str, Any] | None] = []
    params_metadata = getattr(params, "metadata", None)
    if params_metadata:
        sources.append(dict(params_metadata))
    message = getattr(params, "message", None)
    message_metadata = getattr(message, "metadata", None)
    if message_metadata:
        sources.append(_metadata_to_dict(message_metadata))
    return tuple(source for source in sources if source)


def _metadata_field_present(
    sources: Iterable[Mapping[str, Any] | None],
    *,
    namespace: str,
    path: tuple[str, ...],
) -> bool:
    for source in sources:
        if extract_namespaced_value(source, namespace=namespace, path=path) is not None:
            return True
    return False


def _dedupe_requirements(
    requirements: Sequence[ExtensionRequirement],
) -> list[ExtensionRequirement]:
    deduped: list[ExtensionRequirement] = []
    seen: set[tuple[str, str]] = set()
    for requirement in requirements:
        key = (requirement.extension_uri, requirement.field)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(requirement)
    return deduped


def _filter_task(task: Task, requested_extensions: frozenset[str]) -> Task:
    updated = clone_proto(task)
    _set_filtered_metadata(updated, requested_extensions)
    if updated.status.HasField("message"):
        _set_filtered_metadata(updated.status.message, requested_extensions)
    for history_item in updated.history:
        _set_filtered_metadata(history_item, requested_extensions)
    for artifact in updated.artifacts:
        _set_filtered_metadata(artifact, requested_extensions)
    return updated


def _filter_status_update(
    event: TaskStatusUpdateEvent,
    requested_extensions: frozenset[str],
) -> TaskStatusUpdateEvent:
    updated = clone_proto(event)
    _set_filtered_metadata(updated, requested_extensions)
    if updated.status.HasField("message"):
        _set_filtered_metadata(updated.status.message, requested_extensions)
    return updated


def _filter_artifact_update(
    event: TaskArtifactUpdateEvent,
    requested_extensions: frozenset[str],
) -> TaskArtifactUpdateEvent:
    updated = clone_proto(event)
    _set_filtered_metadata(updated.artifact, requested_extensions)
    return updated


def _set_filtered_metadata(
    proto: Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Artifact | Message,
    requested_extensions: frozenset[str],
) -> None:
    metadata_dict = _metadata_to_dict(getattr(proto, "metadata", None))
    filtered_metadata = _filter_metadata_dict(metadata_dict, requested_extensions)
    proto.ClearField("metadata")
    if filtered_metadata:
        proto.metadata.update(filtered_metadata)


def _metadata_to_dict(metadata: Mapping[str, Any] | ProtoMessage | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    if isinstance(metadata, ProtoMessage):
        normalized = proto_to_dict(metadata, preserving_proto_field_name=True)
        return normalized if normalized else None
    if isinstance(metadata, Mapping):
        normalized = dict(metadata)
        return normalized if normalized else None
    return None


def _filter_metadata_dict(
    metadata: Mapping[str, Any] | None,
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

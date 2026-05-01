from __future__ import annotations

from types import MappingProxyType

SHARED_SESSION_BINDING_FIELD = "metadata.shared.session.id"
SHARED_SESSION_METADATA_FIELD = "metadata.shared.session"
SHARED_MODEL_SELECTION_FIELD = "metadata.shared.model"
SHARED_STREAM_METADATA_FIELD = "metadata.shared.stream"
SHARED_PROGRESS_METADATA_FIELD = "metadata.shared.progress"
SHARED_INTERRUPT_METADATA_FIELD = "metadata.shared.interrupt"
SHARED_USAGE_METADATA_FIELD = "metadata.shared.usage"
OPENCODE_DIRECTORY_METADATA_FIELD = "metadata.opencode.directory"
OPENCODE_WORKSPACE_METADATA_FIELD = "metadata.opencode.workspace.id"

EXTENSION_URI_NAMESPACE = "urn:opencode-a2a:extension:"
EXTENSION_SPEC_INDEX_DOCUMENT_PATH = "docs/extension-specifications.md"


def _extension_uri(*segments: str) -> str:
    normalized_segments = [segment.strip("/") for segment in segments if segment.strip("/")]
    return f"{EXTENSION_URI_NAMESPACE}{':'.join(normalized_segments)}"


SESSION_BINDING_EXTENSION_URI = _extension_uri(
    "shared",
    "session-binding",
    "v1",
)
MODEL_SELECTION_EXTENSION_URI = _extension_uri(
    "shared",
    "model-selection",
    "v1",
)
STREAMING_EXTENSION_URI = _extension_uri(
    "shared",
    "stream-hints",
    "v1",
)
SESSION_MANAGEMENT_EXTENSION_URI = _extension_uri(
    "private",
    "session-management",
    "v1",
)
PROVIDER_DISCOVERY_EXTENSION_URI = _extension_uri(
    "private",
    "provider-discovery",
    "v1",
)
INTERRUPT_CALLBACK_EXTENSION_URI = _extension_uri(
    "shared",
    "interactive-interrupt",
    "v1",
)
INTERRUPT_RECOVERY_EXTENSION_URI = _extension_uri(
    "private",
    "interrupt-recovery",
    "v1",
)
WORKSPACE_CONTROL_EXTENSION_URI = _extension_uri(
    "private",
    "workspace-control",
    "v1",
)
COMPATIBILITY_PROFILE_EXTENSION_URI = _extension_uri(
    "private",
    "compatibility-profile",
    "v1",
)
WIRE_CONTRACT_EXTENSION_URI = _extension_uri(
    "private",
    "wire-contract",
    "v1",
)
PUBLIC_EXTENSION_URIS: tuple[str, ...] = (
    SESSION_BINDING_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
)
AUTHENTICATED_ONLY_EXTENSION_URIS: tuple[str, ...] = (
    SESSION_MANAGEMENT_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
)
ALL_EXTENSION_URIS: tuple[str, ...] = PUBLIC_EXTENSION_URIS + AUTHENTICATED_ONLY_EXTENSION_URIS
EXTENSION_SPEC_DOCUMENT_PATHS_BY_URI = MappingProxyType(
    {uri: EXTENSION_SPEC_INDEX_DOCUMENT_PATH for uri in ALL_EXTENSION_URIS}
)
SERVICE_BEHAVIOR_CLASSIFICATION = "service-level-semantic-enhancement"
CANCEL_IDEMPOTENCY_BEHAVIOR = "return_current_terminal_task"
TERMINAL_RESUBSCRIBE_BEHAVIOR = "replay_terminal_task_once_then_close"

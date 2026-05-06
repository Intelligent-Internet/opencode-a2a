from __future__ import annotations

from typing import Any

from ...profile.runtime import RuntimeProfile
from ...protocol_versions import A2A_PROTOCOL_VERSION, normalize_protocol_version
from . import catalog, identifiers
from .capabilities import JsonRpcCapabilitySnapshot, build_capability_snapshot

DECLARED_EXTENSION_URIS: tuple[str, ...] = (
    identifiers.SESSION_BINDING_EXTENSION_URI,
    identifiers.MODEL_SELECTION_EXTENSION_URI,
    identifiers.STREAMING_EXTENSION_URI,
    identifiers.SESSION_MANAGEMENT_EXTENSION_URI,
    identifiers.PROVIDER_DISCOVERY_EXTENSION_URI,
    identifiers.WORKSPACE_CONTROL_EXTENSION_URI,
    identifiers.INTERRUPT_RECOVERY_EXTENSION_URI,
    identifiers.INTERRUPT_CALLBACK_EXTENSION_URI,
)


def _normalize_provider_private_protocol_version(
    value: str,
    *,
    field_name: str,
) -> str:
    try:
        normalized_version = normalize_protocol_version(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must resolve to provider-private A2A protocol version "
            f"{A2A_PROTOCOL_VERSION!r}; got {value!r}."
        ) from exc
    if normalized_version != A2A_PROTOCOL_VERSION:
        raise ValueError(
            f"{field_name} must resolve to provider-private A2A protocol version "
            f"{A2A_PROTOCOL_VERSION!r}; got {value!r}."
        )
    return normalized_version


def _build_provider_private_protocol_versions(
    *,
    protocol_version: str,
    supported_protocol_versions: tuple[str, ...] | list[str] | None,
    default_protocol_version: str | None,
) -> tuple[str, str, list[str]]:
    normalized_protocol_version = _normalize_provider_private_protocol_version(
        protocol_version,
        field_name="protocol_version",
    )
    normalized_default_protocol_version = _normalize_provider_private_protocol_version(
        default_protocol_version or normalized_protocol_version,
        field_name="default_protocol_version",
    )
    declared_supported_protocol_versions = supported_protocol_versions or (
        normalized_default_protocol_version,
    )
    for index, version in enumerate(declared_supported_protocol_versions):
        _normalize_provider_private_protocol_version(
            version,
            field_name=f"supported_protocol_versions[{index}]",
        )
    return (
        normalized_protocol_version,
        normalized_default_protocol_version,
        [A2A_PROTOCOL_VERSION],
    )


def _build_core_contract_surface() -> dict[str, list[str]]:
    return {
        "jsonrpc_methods": list(catalog.CORE_JSONRPC_METHODS),
        "http_endpoints": list(catalog.CORE_HTTP_ENDPOINTS),
    }


def _build_retention_record(
    *,
    surface: str,
    retention: str,
    availability: str = "always",
    extension_uri: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "surface": surface,
        "availability": availability,
        "retention": retention,
    }
    if extension_uri is not None:
        record["extension_uri"] = extension_uri
    record.update(extra)
    return record


def _build_method_retention_records(
    methods: tuple[str, ...] | list[str],
    *,
    extension_uri: str | None = None,
    surface: str = "extension",
    availability: str = "always",
    retention: str,
    **extra: Any,
) -> dict[str, dict[str, Any]]:
    return {
        method: _build_retention_record(
            surface=surface,
            availability=availability,
            retention=retention,
            extension_uri=extension_uri,
            **extra,
        )
        for method in methods
    }


def _build_extension_retention() -> dict[str, dict[str, Any]]:
    return {
        identifiers.SESSION_BINDING_EXTENSION_URI: _build_retention_record(
            surface="core-runtime-metadata",
            retention="required",
        ),
        identifiers.MODEL_SELECTION_EXTENSION_URI: _build_retention_record(
            surface="core-runtime-metadata",
            retention="stable",
        ),
        identifiers.STREAMING_EXTENSION_URI: _build_retention_record(
            surface="core-runtime-metadata",
            retention="required",
        ),
        identifiers.SESSION_MANAGEMENT_EXTENSION_URI: _build_retention_record(
            surface="jsonrpc-extension",
            retention="stable",
        ),
        identifiers.PROVIDER_DISCOVERY_EXTENSION_URI: _build_retention_record(
            surface="jsonrpc-extension",
            retention="stable",
        ),
        identifiers.WORKSPACE_CONTROL_EXTENSION_URI: _build_retention_record(
            surface="jsonrpc-extension",
            retention="mixed",
            upstream_stability="mixed",
        ),
        identifiers.INTERRUPT_RECOVERY_EXTENSION_URI: _build_retention_record(
            surface="jsonrpc-extension",
            retention="stable",
            implementation_scope="adapter-local",
            identity_scope="current_authenticated_caller",
        ),
        identifiers.INTERRUPT_CALLBACK_EXTENSION_URI: _build_retention_record(
            surface="jsonrpc-extension",
            retention="stable",
        ),
    }


def _build_method_retention(
    capability_snapshot: JsonRpcCapabilitySnapshot,
) -> dict[str, dict[str, Any]]:
    method_retention = _build_method_retention_records(
        catalog.CORE_JSONRPC_METHODS,
        surface="core",
        retention="required",
    )
    method_retention.update(
        _build_method_retention_records(
            [method for key, method in catalog.SESSION_METHODS.items() if key != "shell"],
            extension_uri=identifiers.SESSION_MANAGEMENT_EXTENSION_URI,
            retention="stable",
        )
    )
    method_retention.update(capability_snapshot.conditional_method_retention())
    method_retention.update(
        _build_method_retention_records(
            list(catalog.PROVIDER_DISCOVERY_METHODS.values()),
            extension_uri=identifiers.PROVIDER_DISCOVERY_EXTENSION_URI,
            retention="stable",
        )
    )
    method_retention.update(
        _build_method_retention_records(
            list(catalog.WORKSPACE_STABLE_METHODS.values()),
            extension_uri=identifiers.WORKSPACE_CONTROL_EXTENSION_URI,
            retention="stable",
        )
    )
    method_retention.update(
        _build_method_retention_records(
            [
                catalog.WORKSPACE_EXPERIMENTAL_UPSTREAM_METHODS["list_workspaces"],
                catalog.WORKSPACE_EXPERIMENTAL_UPSTREAM_METHODS["list_worktrees"],
            ],
            extension_uri=identifiers.WORKSPACE_CONTROL_EXTENSION_URI,
            retention="experimental-upstream",
        )
    )
    method_retention.update(
        _build_method_retention_records(
            list(catalog.INTERRUPT_RECOVERY_METHODS.values()),
            extension_uri=identifiers.INTERRUPT_RECOVERY_EXTENSION_URI,
            retention="stable",
            implementation_scope="adapter-local",
            identity_scope="current_authenticated_caller",
        )
    )
    for method in catalog.WORKSPACE_MUTATION_METHODS.values():
        retention = method_retention.get(method)
        if retention is not None:
            retention["upstream_stability"] = "experimental"
    method_retention.update(
        _build_method_retention_records(
            list(catalog.INTERRUPT_CALLBACK_METHODS.values()),
            extension_uri=identifiers.INTERRUPT_CALLBACK_EXTENSION_URI,
            retention="stable",
        )
    )
    return method_retention


def build_compatibility_profile_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
    supported_protocol_versions: tuple[str, ...] | list[str] | None = None,
    default_protocol_version: str | None = None,
) -> dict[str, Any]:
    (
        declared_protocol_version,
        declared_default_protocol_version,
        declared_supported_protocol_versions,
    ) = _build_provider_private_protocol_versions(
        protocol_version=protocol_version,
        supported_protocol_versions=supported_protocol_versions,
        default_protocol_version=default_protocol_version,
    )
    protocol_compatibility = build_protocol_compatibility_params(
        supported_protocol_versions=declared_supported_protocol_versions,
        default_protocol_version=declared_default_protocol_version,
    )
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    service_behaviors = build_service_behavior_contract_params()
    return {
        **runtime_profile.summary_dict(protocol_version=declared_protocol_version),
        "default_protocol_version": declared_default_protocol_version,
        "supported_protocol_versions": declared_supported_protocol_versions,
        "protocol_compatibility": protocol_compatibility,
        "core": _build_core_contract_surface(),
        "extension_retention": _build_extension_retention(),
        "method_retention": _build_method_retention(capability_snapshot),
        "service_behaviors": service_behaviors,
        "consumer_guidance": [
            "Treat core A2A methods as the stable interoperability baseline for generic clients.",
            (
                "Treat this deployment as a single-tenant, shared-workspace coding profile; "
                "do not assume per-consumer workspace or tenant isolation."
            ),
            (
                "Treat shared model selection metadata as a stable request-scoped plugin "
                "surface for the main chat path; provider defaults still belong to OpenCode."
            ),
            (
                "Treat opencode.sessions.*, opencode.providers.*, opencode.models.*, "
                "opencode.projects.*, opencode.workspaces.*, opencode.worktrees.*, "
                "opencode.permissions.list, and opencode.questions.list as provider-private "
                "operational surfaces rather than portable A2A baseline capabilities."
            ),
            (
                "Treat a2a.interrupt.* methods as declared shared extensions and opencode.* "
                "methods as vendor-specific extensions that remain stable within the current "
                "major line."
            ),
            (
                "Treat opencode.sessions.shell as deployment-conditional and discover it from "
                "the declared profile and current wire contract before calling it."
            ),
            (
                "Treat opencode.workspaces.create/remove and opencode.worktrees.create/remove/"
                "reset as deployment-conditional operator surfaces rather than baseline "
                "workspace discovery methods."
            ),
            (
                "Treat opencode.workspaces.list and opencode.worktrees.list as declared "
                "adapter contracts over upstream experimental endpoints, not the same "
                "stability tier as project discovery."
            ),
            (
                "Treat opencode.permissions.list and opencode.questions.list as adapter-local, "
                "identity-scoped recovery views rather than upstream global pending queues."
            ),
            (
                "Treat declared service behaviors as stable server-level semantic "
                "enhancements layered on top of the core A2A method baseline."
            ),
            (
                "Treat protocol_compatibility as the runtime truth for which major line "
                "is fully supported by the current deployment."
            ),
        ],
    }


def build_protocol_compatibility_params(
    *,
    supported_protocol_versions: tuple[str, ...] | list[str],
    default_protocol_version: str,
) -> dict[str, Any]:
    declared_supported_versions = list(supported_protocol_versions)
    versions: dict[str, dict[str, Any]] = {
        "1.0": {
            "enabled": "1.0" in declared_supported_versions,
            "default": default_protocol_version == "1.0",
            "status": "supported",
            "supported_features": [
                "Proto-first transport payloads and enum naming.",
                "Canonical A2A v1.0 JSON-RPC method names.",
                "Protocol-aware JSON-RPC and REST error shaping.",
                "Agent Card and OpenAPI discovery aligned to the v1.0 surface.",
            ],
            "known_gaps": [],
        },
    }

    for version in declared_supported_versions:
        if version in versions:
            continue
        versions[version] = {
            "enabled": True,
            "default": default_protocol_version == version,
            "status": "custom",
            "supported_features": [
                "Supported by deployment configuration.",
                "Version-specific compatibility details are not yet declared.",
            ],
            "known_gaps": [
                "This protocol line does not yet have a dedicated compatibility summary.",
            ],
        }

    return {
        "default_protocol_version": default_protocol_version,
        "supported_protocol_versions": declared_supported_versions,
        "versions": versions,
    }


def build_wire_contract_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
    supported_protocol_versions: tuple[str, ...] | list[str] | None = None,
    default_protocol_version: str | None = None,
) -> dict[str, Any]:
    (
        declared_protocol_version,
        declared_default_protocol_version,
        declared_supported_protocol_versions,
    ) = _build_provider_private_protocol_versions(
        protocol_version=protocol_version,
        supported_protocol_versions=supported_protocol_versions,
        default_protocol_version=default_protocol_version,
    )
    protocol_compatibility = build_protocol_compatibility_params(
        supported_protocol_versions=declared_supported_protocol_versions,
        default_protocol_version=declared_default_protocol_version,
    )
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    service_behaviors = build_service_behavior_contract_params()

    return {
        "protocol_version": declared_protocol_version,
        "default_protocol_version": declared_default_protocol_version,
        "supported_protocol_versions": declared_supported_protocol_versions,
        "protocol_compatibility": protocol_compatibility,
        "profile": runtime_profile.summary_dict(protocol_version=declared_protocol_version),
        "preferred_transport": "HTTP+JSON",
        "additional_transports": ["JSON-RPC"],
        "core": _build_core_contract_surface(),
        "extensions": {
            "jsonrpc_methods": capability_snapshot.extension_jsonrpc_methods(),
            "conditionally_available_methods": (
                capability_snapshot.conditionally_available_methods()
            ),
            "extension_uris": list(DECLARED_EXTENSION_URIS),
        },
        "all_jsonrpc_methods": capability_snapshot.supported_jsonrpc_methods(),
        "service_behaviors": service_behaviors,
        "unsupported_method_error": {
            "code": -32601,
            "type": "METHOD_NOT_SUPPORTED",
            "data_fields": list(catalog.WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS),
        },
    }


def build_service_behavior_contract_params() -> dict[str, Any]:
    return {
        "classification": identifiers.SERVICE_BEHAVIOR_CLASSIFICATION,
        "methods": {
            "CancelTask": {
                "baseline": "core",
                "retention": "stable",
                "idempotency": {
                    "already_canceled": {
                        "behavior": identifiers.CANCEL_IDEMPOTENCY_BEHAVIOR,
                        "returns_current_state": "canceled",
                        "error": None,
                    }
                },
            },
            "SubscribeToTask": {
                "baseline": "core",
                "retention": "stable",
                "terminal_state_behavior": {
                    "behavior": identifiers.TERMINAL_RESUBSCRIBE_BEHAVIOR,
                    "delivery": "single_task_snapshot",
                    "closes_stream": True,
                },
            },
        },
    }

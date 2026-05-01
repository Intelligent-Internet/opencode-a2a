from __future__ import annotations

from typing import Any

from ...profile.runtime import RuntimeProfile
from .capabilities import build_capability_snapshot
from .catalog import (
    CORE_HTTP_ENDPOINTS,
    CORE_JSONRPC_METHODS,
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_METHODS,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_METHODS,
    WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS,
    WORKSPACE_EXPERIMENTAL_UPSTREAM_METHODS,
    WORKSPACE_MUTATION_METHODS,
    WORKSPACE_STABLE_METHODS,
)
from .identifiers import (
    CANCEL_IDEMPOTENCY_BEHAVIOR,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SERVICE_BEHAVIOR_CLASSIFICATION,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    TERMINAL_RESUBSCRIBE_BEHAVIOR,
    WORKSPACE_CONTROL_EXTENSION_URI,
)


def build_compatibility_profile_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
    supported_protocol_versions: tuple[str, ...] | list[str] | None = None,
    default_protocol_version: str | None = None,
) -> dict[str, Any]:
    declared_default_protocol_version = default_protocol_version or protocol_version
    declared_supported_protocol_versions = list(
        supported_protocol_versions or (declared_default_protocol_version,)
    )
    protocol_compatibility = build_protocol_compatibility_params(
        supported_protocol_versions=declared_supported_protocol_versions,
        default_protocol_version=declared_default_protocol_version,
    )
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    service_behaviors = build_service_behavior_contract_params()
    method_retention: dict[str, dict[str, Any]] = {
        method: {
            "surface": "core",
            "availability": "always",
            "retention": "required",
        }
        for method in CORE_JSONRPC_METHODS
    }
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": SESSION_MANAGEMENT_EXTENSION_URI,
            }
            for key, method in SESSION_METHODS.items()
            if key != "shell"
        }
    )
    method_retention.update(capability_snapshot.conditional_method_retention())
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": PROVIDER_DISCOVERY_EXTENSION_URI,
            }
            for method in PROVIDER_DISCOVERY_METHODS.values()
        }
    )
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": WORKSPACE_CONTROL_EXTENSION_URI,
            }
            for method in WORKSPACE_STABLE_METHODS.values()
        }
    )
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "experimental-upstream",
                "extension_uri": WORKSPACE_CONTROL_EXTENSION_URI,
            }
            for method in (
                WORKSPACE_EXPERIMENTAL_UPSTREAM_METHODS["list_workspaces"],
                WORKSPACE_EXPERIMENTAL_UPSTREAM_METHODS["list_worktrees"],
            )
        }
    )
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": INTERRUPT_RECOVERY_EXTENSION_URI,
                "implementation_scope": "adapter-local",
                "identity_scope": "current_authenticated_caller",
            }
            for method in INTERRUPT_RECOVERY_METHODS.values()
        }
    )
    for method in WORKSPACE_MUTATION_METHODS.values():
        retention = method_retention.get(method)
        if retention is not None:
            retention["upstream_stability"] = "experimental"
    method_retention.update(
        {
            method: {
                "surface": "extension",
                "availability": "always",
                "retention": "stable",
                "extension_uri": INTERRUPT_CALLBACK_EXTENSION_URI,
            }
            for method in INTERRUPT_CALLBACK_METHODS.values()
        }
    )
    return {
        **runtime_profile.summary_dict(protocol_version=protocol_version),
        "default_protocol_version": declared_default_protocol_version,
        "supported_protocol_versions": declared_supported_protocol_versions,
        "protocol_compatibility": protocol_compatibility,
        "core": {
            "jsonrpc_methods": list(CORE_JSONRPC_METHODS),
            "http_endpoints": list(CORE_HTTP_ENDPOINTS),
        },
        "extension_retention": {
            SESSION_BINDING_EXTENSION_URI: {
                "surface": "core-runtime-metadata",
                "availability": "always",
                "retention": "required",
            },
            MODEL_SELECTION_EXTENSION_URI: {
                "surface": "core-runtime-metadata",
                "availability": "always",
                "retention": "stable",
            },
            STREAMING_EXTENSION_URI: {
                "surface": "core-runtime-metadata",
                "availability": "always",
                "retention": "required",
            },
            SESSION_MANAGEMENT_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
            },
            PROVIDER_DISCOVERY_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
            },
            WORKSPACE_CONTROL_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "mixed",
                "upstream_stability": "mixed",
            },
            INTERRUPT_RECOVERY_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
                "implementation_scope": "adapter-local",
                "identity_scope": "current_authenticated_caller",
            },
            INTERRUPT_CALLBACK_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
            },
        },
        "method_retention": method_retention,
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
    declared_default_protocol_version = default_protocol_version or protocol_version
    declared_supported_protocol_versions = list(
        supported_protocol_versions or (declared_default_protocol_version,)
    )
    protocol_compatibility = build_protocol_compatibility_params(
        supported_protocol_versions=declared_supported_protocol_versions,
        default_protocol_version=declared_default_protocol_version,
    )
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    service_behaviors = build_service_behavior_contract_params()

    return {
        "protocol_version": protocol_version,
        "default_protocol_version": declared_default_protocol_version,
        "supported_protocol_versions": declared_supported_protocol_versions,
        "protocol_compatibility": protocol_compatibility,
        "profile": runtime_profile.summary_dict(protocol_version=protocol_version),
        "preferred_transport": "HTTP+JSON",
        "additional_transports": ["JSON-RPC"],
        "core": {
            "jsonrpc_methods": list(CORE_JSONRPC_METHODS),
            "http_endpoints": list(CORE_HTTP_ENDPOINTS),
        },
        "extensions": {
            "jsonrpc_methods": capability_snapshot.extension_jsonrpc_methods(),
            "conditionally_available_methods": (
                capability_snapshot.conditionally_available_methods()
            ),
            "extension_uris": [
                SESSION_BINDING_EXTENSION_URI,
                MODEL_SELECTION_EXTENSION_URI,
                STREAMING_EXTENSION_URI,
                SESSION_MANAGEMENT_EXTENSION_URI,
                PROVIDER_DISCOVERY_EXTENSION_URI,
                WORKSPACE_CONTROL_EXTENSION_URI,
                INTERRUPT_RECOVERY_EXTENSION_URI,
                INTERRUPT_CALLBACK_EXTENSION_URI,
            ],
        },
        "all_jsonrpc_methods": capability_snapshot.supported_jsonrpc_methods(),
        "service_behaviors": service_behaviors,
        "unsupported_method_error": {
            "code": -32601,
            "type": "METHOD_NOT_SUPPORTED",
            "data_fields": list(WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS),
        },
    }


def build_service_behavior_contract_params() -> dict[str, Any]:
    return {
        "classification": SERVICE_BEHAVIOR_CLASSIFICATION,
        "methods": {
            "CancelTask": {
                "baseline": "core",
                "retention": "stable",
                "idempotency": {
                    "already_canceled": {
                        "behavior": CANCEL_IDEMPOTENCY_BEHAVIOR,
                        "returns_current_state": "canceled",
                        "error": None,
                    }
                },
            },
            "SubscribeToTask": {
                "baseline": "core",
                "retention": "stable",
                "terminal_state_behavior": {
                    "behavior": TERMINAL_RESUBSCRIBE_BEHAVIOR,
                    "delivery": "single_task_snapshot",
                    "closes_stream": True,
                },
            },
        },
    }

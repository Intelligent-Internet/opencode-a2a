from __future__ import annotations

from typing import Any

from ...profile.runtime import RuntimeProfile
from .capabilities import build_capability_snapshot
from .catalog import (
    INTERRUPT_RECOVERY_INVALID_PARAMS_DATA_FIELDS,
    INTERRUPT_RECOVERY_METHOD_CONTRACTS,
    INTERRUPT_RECOVERY_METHODS,
    PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES,
    PROVIDER_DISCOVERY_ERROR_DATA_FIELDS,
    PROVIDER_DISCOVERY_INVALID_PARAMS_DATA_FIELDS,
    PROVIDER_DISCOVERY_METHOD_CONTRACTS,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_CONTROL_METHODS,
    SESSION_METHOD_CONTRACTS,
    SESSION_METHODS,
    SESSION_MUTATION_METHODS,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_ERROR_BUSINESS_CODES,
    SESSION_QUERY_ERROR_DATA_FIELDS,
    SESSION_QUERY_INVALID_PARAMS_DATA_FIELDS,
    SESSION_QUERY_MAX_LIMIT,
    SESSION_QUERY_PAGINATION_BEHAVIOR,
    SESSION_QUERY_PAGINATION_MODE,
    SESSION_QUERY_PAGINATION_PARAMS,
    SESSION_READ_METHODS,
    WORKSPACE_CONTROL_ERROR_BUSINESS_CODES,
    WORKSPACE_CONTROL_ERROR_DATA_FIELDS,
    WORKSPACE_CONTROL_INVALID_PARAMS_DATA_FIELDS,
    WORKSPACE_CONTROL_METHOD_CONTRACTS,
    WORKSPACE_CONTROL_METHODS,
    WORKSPACE_MUTATION_METHODS,
)
from .contract_docs import build_method_contract_docs
from .identifiers import (
    OPENCODE_DIRECTORY_METADATA_FIELD,
    OPENCODE_WORKSPACE_METADATA_FIELD,
    SHARED_SESSION_BINDING_FIELD,
)
from .public_params import (
    PROMPT_ASYNC_PART_CONTRACT_DOC,
    PROMPT_ASYNC_SUBTASK_SUPPORT,
)


def _build_prompt_async_contract_doc() -> dict[str, Any]:
    return {
        "request_parts": PROMPT_ASYNC_PART_CONTRACT_DOC,
        "subtask_support": PROMPT_ASYNC_SUBTASK_SUPPORT,
    }


def build_session_management_extension_params(
    *,
    runtime_profile: RuntimeProfile,
    context_id_prefix: str,
) -> dict[str, Any]:
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    methods = capability_snapshot.session_management_methods()
    read_methods = dict(SESSION_READ_METHODS)
    mutation_methods = dict(SESSION_MUTATION_METHODS)
    control_methods = capability_snapshot.session_control_methods()
    active_session_methods = set(methods.values())
    pagination_applies_to: list[str] = []

    for method_contract in SESSION_METHOD_CONTRACTS.values():
        if method_contract.method not in active_session_methods:
            continue
        if method_contract.pagination_mode == SESSION_QUERY_PAGINATION_MODE:
            pagination_applies_to.append(method_contract.method)

    return {
        "methods": methods,
        "read_methods": read_methods,
        "mutation_methods": mutation_methods,
        "control_methods": control_methods,
        "control_method_flags": capability_snapshot.method_flags(SESSION_CONTROL_METHODS.values()),
        "profile": runtime_profile.summary_dict(),
        "pagination": {
            "mode": SESSION_QUERY_PAGINATION_MODE,
            "default_limit": SESSION_QUERY_DEFAULT_LIMIT,
            "max_limit": SESSION_QUERY_MAX_LIMIT,
            "behavior": SESSION_QUERY_PAGINATION_BEHAVIOR,
            "params": list(SESSION_QUERY_PAGINATION_PARAMS),
            "cursor_param": "before",
            "result_cursor_field": "next_cursor",
            "applies_to": pagination_applies_to,
            "cursor_applies_to": [SESSION_METHODS["get_session_messages"]],
        },
        "method_contracts": build_method_contract_docs(
            SESSION_METHOD_CONTRACTS.values(),
            active_methods=active_session_methods,
            extra_fields_by_method={
                SESSION_METHODS["prompt_async"]: _build_prompt_async_contract_doc(),
            },
        ),
        "errors": {
            "business_codes": dict(SESSION_QUERY_ERROR_BUSINESS_CODES),
            "error_data_fields": list(SESSION_QUERY_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(SESSION_QUERY_INVALID_PARAMS_DATA_FIELDS),
        },
        "context_semantics": {
            "a2a_context_id_field": "contextId",
            "a2a_context_id_prefix": context_id_prefix,
            "upstream_session_id_field": SHARED_SESSION_BINDING_FIELD,
        },
    }


def build_interrupt_recovery_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "methods": dict(INTERRUPT_RECOVERY_METHODS),
        "method_contracts": build_method_contract_docs(
            INTERRUPT_RECOVERY_METHOD_CONTRACTS.values()
        ),
        "supported_metadata": [],
        "provider_private_metadata": [],
        "recovery_scope": {
            "data_source": "local_interrupt_binding_registry",
            "identity_scope": "current_authenticated_caller",
            "empty_result_when_identity_unavailable": True,
        },
        "item_fields": {
            "request_id": "items[].request_id",
            "session_id": "items[].session_id",
            "interrupt_type": "items[].interrupt_type",
            "task_id": "items[].task_id",
            "context_id": "items[].context_id",
            "details": "items[].details",
            "expires_at": "items[].expires_at",
        },
        "errors": {
            "invalid_params_data_fields": list(INTERRUPT_RECOVERY_INVALID_PARAMS_DATA_FIELDS),
        },
        "profile": runtime_profile.summary_dict(),
        "notes": [
            (
                "Interrupt recovery methods read from the local interrupt binding registry "
                "instead of directly proxying upstream global pending lists."
            ),
            (
                "Results are scoped to the current authenticated caller identity when the "
                "runtime can resolve one."
            ),
            (
                "If the runtime cannot resolve a caller identity for the current request, "
                "recovery queries return an empty item list."
            ),
            (
                "Use a2a.interrupt.* methods to resolve requests; opencode.permissions.list "
                "and opencode.questions.list are recovery surfaces only."
            ),
        ],
    }


def build_provider_discovery_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "methods": dict(PROVIDER_DISCOVERY_METHODS),
        "method_contracts": build_method_contract_docs(
            PROVIDER_DISCOVERY_METHOD_CONTRACTS.values()
        ),
        "supported_metadata": ["opencode.directory", "opencode.workspace.id"],
        "provider_private_metadata": ["opencode.directory", "opencode.workspace.id"],
        "context_fields": {
            "directory": OPENCODE_DIRECTORY_METADATA_FIELD,
            "workspace_id": OPENCODE_WORKSPACE_METADATA_FIELD,
        },
        "provider_item_fields": {
            "provider_id": "items[].provider_id",
            "name": "items[].name",
            "source": "items[].source",
            "connected": "items[].connected",
            "default_model_id": "items[].default_model_id",
            "model_count": "items[].model_count",
        },
        "model_item_fields": {
            "provider_id": "items[].provider_id",
            "model_id": "items[].model_id",
            "name": "items[].name",
            "status": "items[].status",
            "context_window": "items[].context_window",
            "supports_reasoning": "items[].supports_reasoning",
            "supports_tool_call": "items[].supports_tool_call",
            "supports_attachments": "items[].supports_attachments",
            "default": "items[].default",
            "connected": "items[].connected",
        },
        "errors": {
            "business_codes": dict(PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES),
            "error_data_fields": list(PROVIDER_DISCOVERY_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(PROVIDER_DISCOVERY_INVALID_PARAMS_DATA_FIELDS),
        },
        "profile": runtime_profile.summary_dict(),
        "notes": [
            (
                "Provider/model discovery is OpenCode-specific and exposed through "
                "provider-private JSON-RPC methods."
            ),
            (
                "The server normalizes upstream provider catalogs into summary records so "
                "downstream callers do not need to parse raw OpenCode payloads."
            ),
            (
                "If metadata.opencode.workspace.id is present, provider/model discovery is "
                "routed to that workspace; otherwise the adapter falls back to directory "
                "routing when metadata.opencode.directory is provided."
            ),
        ],
    }


def build_workspace_control_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    methods = capability_snapshot.workspace_control_methods()
    active_workspace_methods = set(methods.values())

    return {
        "methods": methods,
        "control_method_flags": capability_snapshot.method_flags(
            WORKSPACE_MUTATION_METHODS.values()
        ),
        "method_contracts": build_method_contract_docs(
            WORKSPACE_CONTROL_METHOD_CONTRACTS.values(),
            active_methods=active_workspace_methods,
        ),
        "upstream_stability": {
            WORKSPACE_CONTROL_METHODS["list_projects"]: "stable",
            WORKSPACE_CONTROL_METHODS["get_current_project"]: "stable",
            WORKSPACE_CONTROL_METHODS["list_workspaces"]: "experimental",
            WORKSPACE_CONTROL_METHODS["list_worktrees"]: "experimental",
            WORKSPACE_CONTROL_METHODS["create_workspace"]: "experimental",
            WORKSPACE_CONTROL_METHODS["remove_workspace"]: "experimental",
            WORKSPACE_CONTROL_METHODS["create_worktree"]: "experimental",
            WORKSPACE_CONTROL_METHODS["remove_worktree"]: "experimental",
            WORKSPACE_CONTROL_METHODS["reset_worktree"]: "experimental",
        },
        "supported_metadata": ["opencode.workspace.id", "opencode.directory"],
        "provider_private_metadata": ["opencode.workspace.id", "opencode.directory"],
        "routing_fields": {
            "workspace_id": OPENCODE_WORKSPACE_METADATA_FIELD,
            "directory": OPENCODE_DIRECTORY_METADATA_FIELD,
        },
        "errors": {
            "business_codes": dict(WORKSPACE_CONTROL_ERROR_BUSINESS_CODES),
            "error_data_fields": list(WORKSPACE_CONTROL_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(WORKSPACE_CONTROL_INVALID_PARAMS_DATA_FIELDS),
        },
        "profile": runtime_profile.summary_dict(),
        "notes": [
            (
                "Workspace control methods expose the OpenCode project/workspace/worktree "
                "control plane through provider-private JSON-RPC methods."
            ),
            (
                "Mutation methods are deployment-conditional and disabled by default; "
                "discover availability from the declared wire contract before calling them."
            ),
            (
                "Workspace routing metadata is declared for consistency, but the current "
                "control-plane methods operate on the active deployment project rather than "
                "per-request workspace forwarding."
            ),
            (
                "Workspace/worktree discovery and mutation methods currently wrap upstream "
                "/experimental/workspace and /experimental/worktree endpoints."
            ),
        ],
    }

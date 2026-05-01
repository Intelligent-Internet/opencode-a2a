from __future__ import annotations

from typing import Any

from ...profile.runtime import RuntimeProfile
from .catalog import (
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_CALLBACK_METHOD_CONTRACTS,
    INTERRUPT_ERROR_BUSINESS_CODES,
    INTERRUPT_ERROR_DATA_FIELDS,
    INTERRUPT_ERROR_TYPES,
    INTERRUPT_INVALID_PARAMS_DATA_FIELDS,
    INTERRUPT_SUCCESS_RESULT_FIELDS,
    PROMPT_ASYNC_PART_CONTRACTS,
    PROMPT_ASYNC_SUPPORTED_PART_TYPES,
)
from .identifiers import (
    OPENCODE_DIRECTORY_METADATA_FIELD,
    OPENCODE_WORKSPACE_METADATA_FIELD,
    SHARED_INTERRUPT_METADATA_FIELD,
    SHARED_MODEL_SELECTION_FIELD,
    SHARED_PROGRESS_METADATA_FIELD,
    SHARED_SESSION_BINDING_FIELD,
    SHARED_SESSION_METADATA_FIELD,
    SHARED_STREAM_METADATA_FIELD,
    SHARED_USAGE_METADATA_FIELD,
)

def _build_method_contract_params(
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    unsupported: tuple[str, ...],
) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    if required:
        params["required"] = list(required)
    if optional:
        params["optional"] = list(optional)
    if unsupported:
        params["unsupported"] = list(unsupported)
    return params


def _build_prompt_async_part_contracts() -> dict[str, Any]:
    part_contracts: dict[str, Any] = {}
    for part_type, contract in PROMPT_ASYNC_PART_CONTRACTS.items():
        part_contract_doc: dict[str, Any] = {
            "required": list(contract["required"]),
        }
        optional = contract.get("optional")
        if optional:
            part_contract_doc["optional"] = list(optional)
        part_contracts[part_type] = part_contract_doc
    return {
        "items_type": "PromptAsyncPart[]",
        "type_field": "type",
        "accepted_types": list(PROMPT_ASYNC_SUPPORTED_PART_TYPES),
        "part_contracts": part_contracts,
    }


def _build_prompt_async_subtask_support() -> dict[str, Any]:
    return {
        "support_level": "passthrough-compatible",
        "invocation_path": "request.parts[]",
        "part_type": "subtask",
        "subagent_selector_field": "request.parts[].agent",
        "execution_model": "upstream-provider-private-subagent-runtime",
        "notes": [
            (
                "opencode-a2a validates and forwards provider-private subtask parts to "
                "the upstream OpenCode session runtime."
            ),
            (
                "The adapter does not define a separate subagent discovery or "
                "orchestration JSON-RPC method surface."
            ),
            (
                "Subtask execution semantics, available subagent names, and any task-tool "
                "fan-out remain upstream OpenCode behavior."
            ),
        ],
    }


def build_session_binding_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "metadata_field": SHARED_SESSION_BINDING_FIELD,
        "behavior": "prefer_metadata_binding_else_create_session",
        "supported_metadata": [
            "shared.session.id",
            "opencode.directory",
            "opencode.workspace.id",
        ],
        "provider_private_metadata": ["opencode.directory", "opencode.workspace.id"],
        "profile": runtime_profile.summary_dict(),
        "notes": [
            (
                "If metadata.shared.session.id is provided, the server will send the "
                "message to that upstream session."
            ),
            (
                "Otherwise, the server will create a new upstream session and retain "
                "the (identity, contextId)->session_id mapping according to the "
                "configured task/state store backend and TTL policy."
            ),
            (
                "If metadata.opencode.workspace.id is provided, the server routes the "
                "request with workspace precedence and falls back to directory binding only "
                "when workspace metadata is absent."
            ),
        ],
    }


def build_model_selection_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "metadata_field": SHARED_MODEL_SELECTION_FIELD,
        "behavior": "prefer_metadata_model_else_upstream_default",
        "applies_to_methods": ["SendMessage", "SendStreamingMessage"],
        "supported_metadata": [
            "shared.model.providerID",
            "shared.model.modelID",
        ],
        "provider_private_metadata": [],
        "profile": runtime_profile.summary_dict(),
        "fields": {
            "providerID": f"{SHARED_MODEL_SELECTION_FIELD}.providerID",
            "modelID": f"{SHARED_MODEL_SELECTION_FIELD}.modelID",
        },
        "notes": [
            (
                "If both metadata.shared.model.providerID and metadata.shared.model.modelID "
                "are non-empty strings, the server will override the upstream model for "
                "this request only."
            ),
            (
                "If shared model metadata is missing, partial, or invalid, the server "
                "falls back to the upstream OpenCode default behavior."
            ),
        ],
    }


def build_streaming_extension_params() -> dict[str, Any]:
    return {
        "artifact_metadata_field": SHARED_STREAM_METADATA_FIELD,
        "status_metadata_field": SHARED_STREAM_METADATA_FIELD,
        "progress_metadata_field": SHARED_PROGRESS_METADATA_FIELD,
        "interrupt_metadata_field": SHARED_INTERRUPT_METADATA_FIELD,
        "session_metadata_field": SHARED_SESSION_METADATA_FIELD,
        "usage_metadata_field": SHARED_USAGE_METADATA_FIELD,
        "block_types": ["text", "reasoning", "tool_call"],
        "block_contracts": {
            "text": {
                "part_kind": "text",
                "payload_field": "artifact.parts[].text",
            },
            "reasoning": {
                "part_kind": "text",
                "payload_field": "artifact.parts[].text",
            },
            "tool_call": {
                "part_kind": "data",
                "payload_field": "artifact.parts[].data",
                "payload_fields": {
                    "call_id": "artifact.parts[].data.call_id",
                    "tool": "artifact.parts[].data.tool",
                    "status": "artifact.parts[].data.status",
                    "title": "artifact.parts[].data.title",
                    "subtitle": "artifact.parts[].data.subtitle",
                    "input": "artifact.parts[].data.input",
                    "output": "artifact.parts[].data.output",
                    "error": "artifact.parts[].data.error",
                },
            },
        },
        "stream_fields": {
            "block_type": f"{SHARED_STREAM_METADATA_FIELD}.block_type",
            "source": f"{SHARED_STREAM_METADATA_FIELD}.source",
            "message_id": f"{SHARED_STREAM_METADATA_FIELD}.message_id",
            "event_id": f"{SHARED_STREAM_METADATA_FIELD}.event_id",
            "sequence": f"{SHARED_STREAM_METADATA_FIELD}.sequence",
            "role": f"{SHARED_STREAM_METADATA_FIELD}.role",
        },
        "progress_fields": {
            "type": f"{SHARED_PROGRESS_METADATA_FIELD}.type",
            "part_id": f"{SHARED_PROGRESS_METADATA_FIELD}.part_id",
            "reason": f"{SHARED_PROGRESS_METADATA_FIELD}.reason",
            "status": f"{SHARED_PROGRESS_METADATA_FIELD}.status",
            "title": f"{SHARED_PROGRESS_METADATA_FIELD}.title",
            "subtitle": f"{SHARED_PROGRESS_METADATA_FIELD}.subtitle",
        },
        "interrupt_fields": {
            "request_id": f"{SHARED_INTERRUPT_METADATA_FIELD}.request_id",
            "type": f"{SHARED_INTERRUPT_METADATA_FIELD}.type",
            "phase": f"{SHARED_INTERRUPT_METADATA_FIELD}.phase",
            "details": f"{SHARED_INTERRUPT_METADATA_FIELD}.details",
            "resolution": f"{SHARED_INTERRUPT_METADATA_FIELD}.resolution",
        },
        "session_fields": {
            "id": f"{SHARED_SESSION_METADATA_FIELD}.id",
            "title": f"{SHARED_SESSION_METADATA_FIELD}.title",
        },
        "usage_fields": {
            "input_tokens": f"{SHARED_USAGE_METADATA_FIELD}.input_tokens",
            "output_tokens": f"{SHARED_USAGE_METADATA_FIELD}.output_tokens",
            "total_tokens": f"{SHARED_USAGE_METADATA_FIELD}.total_tokens",
            "reasoning_tokens": f"{SHARED_USAGE_METADATA_FIELD}.reasoning_tokens",
            "cost": f"{SHARED_USAGE_METADATA_FIELD}.cost",
            "cache_tokens": {
                "read_tokens": f"{SHARED_USAGE_METADATA_FIELD}.cache_tokens.read_tokens",
                "write_tokens": f"{SHARED_USAGE_METADATA_FIELD}.cache_tokens.write_tokens",
            },
        },
    }


def build_interrupt_callback_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    method_contracts: dict[str, Any] = {}
    for contract in INTERRUPT_CALLBACK_METHOD_CONTRACTS.values():
        method_contract_doc: dict[str, Any] = {
            "params": _build_method_contract_params(
                required=contract.required_params,
                optional=contract.optional_params,
                unsupported=(),
            ),
            "result": {"fields": list(INTERRUPT_SUCCESS_RESULT_FIELDS)},
        }
        if contract.notification_response_status is not None:
            method_contract_doc["notification_response_status"] = (
                contract.notification_response_status
            )
        method_contracts[contract.method] = method_contract_doc

    return {
        "methods": dict(INTERRUPT_CALLBACK_METHODS),
        "method_contracts": method_contracts,
        "supported_interrupt_events": [
            "permission.asked",
            "question.asked",
        ],
        "permission_reply_values": ["once", "always", "reject"],
        "question_reply_contract": {
            "answers": "array of answer arrays (same order as asked questions)"
        },
        "request_id_field": f"{SHARED_INTERRUPT_METADATA_FIELD}.request_id",
        "supported_metadata": ["opencode.directory", "opencode.workspace.id"],
        "provider_private_metadata": ["opencode.directory", "opencode.workspace.id"],
        "context_fields": {
            "directory": OPENCODE_DIRECTORY_METADATA_FIELD,
            "workspace_id": OPENCODE_WORKSPACE_METADATA_FIELD,
        },
        "success_result_fields": list(INTERRUPT_SUCCESS_RESULT_FIELDS),
        "errors": {
            "business_codes": dict(INTERRUPT_ERROR_BUSINESS_CODES),
            "error_types": list(INTERRUPT_ERROR_TYPES),
            "error_data_fields": list(INTERRUPT_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(INTERRUPT_INVALID_PARAMS_DATA_FIELDS),
        },
        "profile": runtime_profile.summary_dict(),
    }

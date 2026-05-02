from __future__ import annotations

from typing import Any

from ...profile.runtime import RuntimeProfile
from . import catalog, identifiers
from .contract_docs import build_method_contract_docs

PROMPT_ASYNC_PART_CONTRACT_DOC = {
    "items_type": "PromptAsyncPart[]",
    "type_field": "type",
    "accepted_types": list(catalog.PROMPT_ASYNC_SUPPORTED_PART_TYPES),
    "part_contracts": {
        part_type: {
            "required": list(contract["required"]),
            **(
                {"optional": list(optional)}
                if (optional := contract.get("optional")) is not None
                else {}
            ),
        }
        for part_type, contract in catalog.PROMPT_ASYNC_PART_CONTRACTS.items()
    },
}
PROMPT_ASYNC_SUBTASK_SUPPORT = {
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


def select_public_extension_params(
    params: dict[str, Any],
    *,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {key: params[key] for key in keys if key in params}


def build_public_streaming_extension_params(
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_metadata_field": params["artifact_metadata_field"],
        "progress_metadata_field": params["progress_metadata_field"],
        "usage_metadata_field": params["usage_metadata_field"],
        "block_types": params["block_types"],
        "stream_fields": select_public_extension_params(
            params["stream_fields"],
            keys=("block_type", "sequence"),
        ),
        "progress_fields": select_public_extension_params(
            params["progress_fields"],
            keys=("type", "status"),
        ),
        "usage_fields": select_public_extension_params(
            params["usage_fields"],
            keys=("input_tokens", "output_tokens", "total_tokens"),
        ),
    }


def build_public_interrupt_callback_extension_params(
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "methods": params["methods"],
        "supported_interrupt_events": params["supported_interrupt_events"],
        "request_id_field": params["request_id_field"],
        "interrupt_metadata_field": params["interrupt_metadata_field"],
        "interrupt_fields": select_public_extension_params(
            params["interrupt_fields"],
            keys=("request_id", "type", "phase"),
        ),
    }


def build_public_session_binding_extension_params(
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "metadata_field": params["metadata_field"],
        "session_metadata_field": params["session_metadata_field"],
        "behavior": params["behavior"],
        "supported_metadata": params["supported_metadata"],
        "session_fields": select_public_extension_params(
            params["session_fields"],
            keys=("id",),
        ),
        "provider_private_metadata": params["provider_private_metadata"],
    }


def build_session_binding_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "metadata_field": identifiers.SHARED_SESSION_BINDING_FIELD,
        "session_metadata_field": identifiers.SHARED_SESSION_METADATA_FIELD,
        "behavior": "prefer_metadata_binding_else_create_session",
        "supported_metadata": [
            "shared.session.id",
            "opencode.directory",
            "opencode.workspace.id",
        ],
        "session_fields": {
            "id": f"{identifiers.SHARED_SESSION_METADATA_FIELD}.id",
            "title": f"{identifiers.SHARED_SESSION_METADATA_FIELD}.title",
        },
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
        "metadata_field": identifiers.SHARED_MODEL_SELECTION_FIELD,
        "behavior": "prefer_metadata_model_else_upstream_default",
        "applies_to_methods": ["SendMessage", "SendStreamingMessage"],
        "supported_metadata": [
            "shared.model.providerID",
            "shared.model.modelID",
        ],
        "provider_private_metadata": [],
        "profile": runtime_profile.summary_dict(),
        "fields": {
            "providerID": f"{identifiers.SHARED_MODEL_SELECTION_FIELD}.providerID",
            "modelID": f"{identifiers.SHARED_MODEL_SELECTION_FIELD}.modelID",
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
        "artifact_metadata_field": identifiers.SHARED_STREAM_METADATA_FIELD,
        "status_metadata_field": identifiers.SHARED_STREAM_METADATA_FIELD,
        "progress_metadata_field": identifiers.SHARED_PROGRESS_METADATA_FIELD,
        "usage_metadata_field": identifiers.SHARED_USAGE_METADATA_FIELD,
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
            "block_type": f"{identifiers.SHARED_STREAM_METADATA_FIELD}.block_type",
            "message_id": f"{identifiers.SHARED_STREAM_METADATA_FIELD}.message_id",
            "event_id": f"{identifiers.SHARED_STREAM_METADATA_FIELD}.event_id",
            "sequence": f"{identifiers.SHARED_STREAM_METADATA_FIELD}.sequence",
        },
        "progress_fields": {
            "type": f"{identifiers.SHARED_PROGRESS_METADATA_FIELD}.type",
            "part_id": f"{identifiers.SHARED_PROGRESS_METADATA_FIELD}.part_id",
            "reason": f"{identifiers.SHARED_PROGRESS_METADATA_FIELD}.reason",
            "status": f"{identifiers.SHARED_PROGRESS_METADATA_FIELD}.status",
            "title": f"{identifiers.SHARED_PROGRESS_METADATA_FIELD}.title",
            "subtitle": f"{identifiers.SHARED_PROGRESS_METADATA_FIELD}.subtitle",
        },
        "usage_fields": {
            "input_tokens": f"{identifiers.SHARED_USAGE_METADATA_FIELD}.input_tokens",
            "output_tokens": f"{identifiers.SHARED_USAGE_METADATA_FIELD}.output_tokens",
            "total_tokens": f"{identifiers.SHARED_USAGE_METADATA_FIELD}.total_tokens",
            "reasoning_tokens": (f"{identifiers.SHARED_USAGE_METADATA_FIELD}.reasoning_tokens"),
            "cost": f"{identifiers.SHARED_USAGE_METADATA_FIELD}.cost",
            "cache_tokens": {
                "read_tokens": (
                    f"{identifiers.SHARED_USAGE_METADATA_FIELD}.cache_tokens.read_tokens"
                ),
                "write_tokens": (
                    f"{identifiers.SHARED_USAGE_METADATA_FIELD}.cache_tokens.write_tokens"
                ),
            },
        },
    }


def build_interrupt_callback_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    return {
        "methods": dict(catalog.INTERRUPT_CALLBACK_METHODS),
        "method_contracts": build_method_contract_docs(
            catalog.INTERRUPT_CALLBACK_METHOD_CONTRACTS.values(),
            default_result_fields=catalog.INTERRUPT_SUCCESS_RESULT_FIELDS,
        ),
        "supported_interrupt_events": [
            "permission.asked",
            "question.asked",
        ],
        "permission_reply_values": ["once", "always", "reject"],
        "question_reply_contract": {
            "answers": "array of answer arrays (same order as asked questions)"
        },
        "request_id_field": f"{identifiers.SHARED_INTERRUPT_METADATA_FIELD}.request_id",
        "interrupt_metadata_field": identifiers.SHARED_INTERRUPT_METADATA_FIELD,
        "interrupt_fields": {
            "request_id": f"{identifiers.SHARED_INTERRUPT_METADATA_FIELD}.request_id",
            "type": f"{identifiers.SHARED_INTERRUPT_METADATA_FIELD}.type",
            "phase": f"{identifiers.SHARED_INTERRUPT_METADATA_FIELD}.phase",
            "details": f"{identifiers.SHARED_INTERRUPT_METADATA_FIELD}.details",
            "resolution": f"{identifiers.SHARED_INTERRUPT_METADATA_FIELD}.resolution",
        },
        "supported_metadata": ["opencode.directory", "opencode.workspace.id"],
        "provider_private_metadata": ["opencode.directory", "opencode.workspace.id"],
        "context_fields": {
            "directory": identifiers.OPENCODE_DIRECTORY_METADATA_FIELD,
            "workspace_id": identifiers.OPENCODE_WORKSPACE_METADATA_FIELD,
        },
        "success_result_fields": list(catalog.INTERRUPT_SUCCESS_RESULT_FIELDS),
        "errors": {
            "business_codes": dict(catalog.INTERRUPT_ERROR_BUSINESS_CODES),
            "error_types": list(catalog.INTERRUPT_ERROR_TYPES),
            "error_data_fields": list(catalog.INTERRUPT_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(catalog.INTERRUPT_INVALID_PARAMS_DATA_FIELDS),
        },
        "profile": runtime_profile.summary_dict(),
    }

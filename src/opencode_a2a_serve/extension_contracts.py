from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionQueryMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    unsupported_params: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    items_type: str | None = None
    items_field: str | None = None
    notification_response_status: int | None = None
    pagination_mode: str | None = None


@dataclass(frozen=True)
class InterruptMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    notification_response_status: int | None = None


PROMPT_ASYNC_REQUEST_REQUIRED_FIELDS: tuple[str, ...] = ("parts",)
PROMPT_ASYNC_REQUEST_OPTIONAL_FIELDS: tuple[str, ...] = (
    "messageID",
    "model",
    "agent",
    "noReply",
    "tools",
    "format",
    "system",
    "variant",
)
PROMPT_ASYNC_REQUEST_ALLOWED_FIELDS: tuple[str, ...] = (
    *PROMPT_ASYNC_REQUEST_REQUIRED_FIELDS,
    *PROMPT_ASYNC_REQUEST_OPTIONAL_FIELDS,
)
COMMAND_REQUEST_REQUIRED_FIELDS: tuple[str, ...] = ("command", "arguments")
COMMAND_REQUEST_OPTIONAL_FIELDS: tuple[str, ...] = (
    "messageID",
    "agent",
    "model",
    "variant",
    "parts",
)
COMMAND_REQUEST_ALLOWED_FIELDS: tuple[str, ...] = (
    *COMMAND_REQUEST_REQUIRED_FIELDS,
    *COMMAND_REQUEST_OPTIONAL_FIELDS,
)
SHELL_REQUEST_REQUIRED_FIELDS: tuple[str, ...] = ("agent", "command")
SHELL_REQUEST_OPTIONAL_FIELDS: tuple[str, ...] = ("model",)
SHELL_REQUEST_ALLOWED_FIELDS: tuple[str, ...] = (
    *SHELL_REQUEST_REQUIRED_FIELDS,
    *SHELL_REQUEST_OPTIONAL_FIELDS,
)

SESSION_QUERY_PAGINATION_MODE = "limit"
SESSION_QUERY_PAGINATION_BEHAVIOR = "passthrough"
SESSION_QUERY_PAGINATION_PARAMS: tuple[str, ...] = ("limit",)
SESSION_QUERY_PAGINATION_UNSUPPORTED: tuple[str, ...] = ("cursor", "page", "size")

SESSION_QUERY_METHOD_CONTRACTS: dict[str, SessionQueryMethodContract] = {
    "list_sessions": SessionQueryMethodContract(
        method="opencode.sessions.list",
        optional_params=("limit", "query.limit"),
        unsupported_params=SESSION_QUERY_PAGINATION_UNSUPPORTED,
        result_fields=("items",),
        items_type="Task[]",
        items_field="items",
        notification_response_status=204,
        pagination_mode=SESSION_QUERY_PAGINATION_MODE,
    ),
    "get_session_messages": SessionQueryMethodContract(
        method="opencode.sessions.messages.list",
        required_params=("session_id",),
        optional_params=("limit", "query.limit"),
        unsupported_params=SESSION_QUERY_PAGINATION_UNSUPPORTED,
        result_fields=("items",),
        items_type="Message[]",
        items_field="items",
        notification_response_status=204,
        pagination_mode=SESSION_QUERY_PAGINATION_MODE,
    ),
    "prompt_async": SessionQueryMethodContract(
        method="opencode.sessions.prompt_async",
        required_params=("session_id", "request.parts"),
        optional_params=(
            "request.messageID",
            "request.model",
            "request.agent",
            "request.noReply",
            "request.tools",
            "request.format",
            "request.system",
            "request.variant",
            "metadata.opencode.directory",
        ),
        result_fields=("ok", "session_id"),
        notification_response_status=204,
    ),
    "command": SessionQueryMethodContract(
        method="opencode.sessions.command",
        required_params=("session_id", "request.command", "request.arguments"),
        optional_params=(
            "request.messageID",
            "request.agent",
            "request.model",
            "request.variant",
            "request.parts",
            "metadata.opencode.directory",
        ),
        result_fields=("item",),
        notification_response_status=204,
    ),
    "shell": SessionQueryMethodContract(
        method="opencode.sessions.shell",
        required_params=("session_id", "request.agent", "request.command"),
        optional_params=(
            "request.model",
            "metadata.opencode.directory",
        ),
        result_fields=("item",),
        notification_response_status=204,
    ),
}

SESSION_QUERY_METHODS: dict[str, str] = {
    key: contract.method for key, contract in SESSION_QUERY_METHOD_CONTRACTS.items()
}
SESSION_CONTROL_METHOD_KEYS: tuple[str, ...] = ("prompt_async", "command", "shell")
SESSION_CONTROL_METHODS: dict[str, str] = {
    key: SESSION_QUERY_METHODS[key] for key in SESSION_CONTROL_METHOD_KEYS
}

SESSION_QUERY_ERROR_BUSINESS_CODES: dict[str, int] = {
    "SESSION_NOT_FOUND": -32001,
    "SESSION_FORBIDDEN": -32006,
    "METHOD_DISABLED": -32007,
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
    "UPSTREAM_PAYLOAD_ERROR": -32005,
}
SESSION_QUERY_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "session_id",
    "upstream_status",
    "detail",
)
SESSION_QUERY_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
    "supported",
    "unsupported",
)

INTERRUPT_CALLBACK_METHOD_CONTRACTS: dict[str, InterruptMethodContract] = {
    "reply_permission": InterruptMethodContract(
        method="opencode.permission.reply",
        required_params=("request_id", "reply"),
        optional_params=("message", "metadata"),
        notification_response_status=204,
    ),
    "reply_question": InterruptMethodContract(
        method="opencode.question.reply",
        required_params=("request_id", "answers"),
        optional_params=("metadata",),
        notification_response_status=204,
    ),
    "reject_question": InterruptMethodContract(
        method="opencode.question.reject",
        required_params=("request_id",),
        optional_params=("metadata",),
        notification_response_status=204,
    ),
}

INTERRUPT_CALLBACK_METHODS: dict[str, str] = {
    key: contract.method for key, contract in INTERRUPT_CALLBACK_METHOD_CONTRACTS.items()
}

INTERRUPT_SUCCESS_RESULT_FIELDS: tuple[str, ...] = ("ok", "request_id")
INTERRUPT_ERROR_BUSINESS_CODES: dict[str, int] = {
    "INTERRUPT_REQUEST_NOT_FOUND": -32004,
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
}
INTERRUPT_ERROR_TYPES: tuple[str, ...] = (
    "INTERRUPT_REQUEST_NOT_FOUND",
    "INTERRUPT_REQUEST_EXPIRED",
    "INTERRUPT_TYPE_MISMATCH",
    "UPSTREAM_UNREACHABLE",
    "UPSTREAM_HTTP_ERROR",
)
INTERRUPT_ERROR_DATA_FIELDS: tuple[str, ...] = ("type", "request_id", "upstream_status")
INTERRUPT_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
    "request_id",
    "expected",
    "actual",
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


def build_session_query_extension_params(
    *,
    deployment_context: dict[str, str | bool],
    context_id_prefix: str,
) -> dict[str, Any]:
    method_contracts: dict[str, Any] = {}
    result_envelope_by_method: dict[str, Any] = {}
    pagination_applies_to: list[str] = []

    for method_contract in SESSION_QUERY_METHOD_CONTRACTS.values():
        params_contract = _build_method_contract_params(
            required=method_contract.required_params,
            optional=method_contract.optional_params,
            unsupported=method_contract.unsupported_params,
        )
        result_contract: dict[str, Any] = {"fields": list(method_contract.result_fields)}
        if method_contract.items_type:
            result_contract["items_type"] = method_contract.items_type

        contract_doc: dict[str, Any] = {
            "params": params_contract,
            "result": result_contract,
        }
        if method_contract.notification_response_status is not None:
            contract_doc["notification_response_status"] = (
                method_contract.notification_response_status
            )
        method_contracts[method_contract.method] = contract_doc

        envelope_doc: dict[str, Any] = {"fields": list(method_contract.result_fields)}
        if method_contract.items_field:
            envelope_doc["items_field"] = method_contract.items_field
        result_envelope_by_method[method_contract.method] = envelope_doc

        if method_contract.pagination_mode == SESSION_QUERY_PAGINATION_MODE:
            pagination_applies_to.append(method_contract.method)

    return {
        "methods": dict(SESSION_QUERY_METHODS),
        "control_methods": dict(SESSION_CONTROL_METHODS),
        "control_method_flags": {
            SESSION_QUERY_METHODS["shell"]: {
                "enabled_by_default": False,
                "config_key": "A2A_ENABLE_SESSION_SHELL",
            }
        },
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
        "deployment_context": deployment_context,
        "pagination": {
            "mode": SESSION_QUERY_PAGINATION_MODE,
            "behavior": SESSION_QUERY_PAGINATION_BEHAVIOR,
            "params": list(SESSION_QUERY_PAGINATION_PARAMS),
            "applies_to": pagination_applies_to,
        },
        "method_contracts": method_contracts,
        "errors": {
            "business_codes": dict(SESSION_QUERY_ERROR_BUSINESS_CODES),
            "error_data_fields": list(SESSION_QUERY_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(SESSION_QUERY_INVALID_PARAMS_DATA_FIELDS),
        },
        "result_envelope": {
            "by_method": result_envelope_by_method,
        },
        "context_semantics": {
            "a2a_context_id_field": "contextId",
            "a2a_context_id_prefix": context_id_prefix,
            "upstream_session_id_field": "metadata.opencode.session_id",
        },
    }


def build_interrupt_callback_extension_params(
    *,
    deployment_context: dict[str, str | bool],
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
        "metadata_namespace": "opencode",
        "supported_metadata": ["opencode.directory"],
        "context_fields": {
            "directory": "metadata.opencode.directory",
        },
        "success_result_fields": list(INTERRUPT_SUCCESS_RESULT_FIELDS),
        "errors": {
            "business_codes": dict(INTERRUPT_ERROR_BUSINESS_CODES),
            "error_types": list(INTERRUPT_ERROR_TYPES),
            "error_data_fields": list(INTERRUPT_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(INTERRUPT_INVALID_PARAMS_DATA_FIELDS),
        },
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
        "deployment_context": deployment_context,
    }

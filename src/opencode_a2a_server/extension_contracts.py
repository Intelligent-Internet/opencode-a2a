from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runtime_profile import RuntimeProfile, build_contract_profile_metadata

SHARED_SESSION_BINDING_FIELD = "metadata.shared.session.id"
SHARED_SESSION_METADATA_FIELD = "metadata.shared.session"
SHARED_MODEL_SELECTION_FIELD = "metadata.shared.model"
SHARED_STREAM_METADATA_FIELD = "metadata.shared.stream"
SHARED_PROGRESS_METADATA_FIELD = "metadata.shared.progress"
SHARED_INTERRUPT_METADATA_FIELD = "metadata.shared.interrupt"
SHARED_USAGE_METADATA_FIELD = "metadata.shared.usage"
OPENCODE_DIRECTORY_METADATA_FIELD = "metadata.opencode.directory"

SESSION_BINDING_EXTENSION_URI = "urn:a2a:session-binding/v1"
MODEL_SELECTION_EXTENSION_URI = "urn:a2a:model-selection/v1"
STREAMING_EXTENSION_URI = "urn:a2a:stream-hints/v1"
SESSION_QUERY_EXTENSION_URI = "urn:opencode-a2a:session-query/v1"
PROVIDER_DISCOVERY_EXTENSION_URI = "urn:opencode-a2a:provider-discovery/v1"
INTERRUPT_CALLBACK_EXTENSION_URI = "urn:a2a:interactive-interrupt/v1"
COMPATIBILITY_PROFILE_EXTENSION_URI = "urn:a2a:compatibility-profile/v1"
WIRE_CONTRACT_EXTENSION_URI = "urn:a2a:wire-contract/v1"


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


@dataclass(frozen=True)
class ProviderDiscoveryMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    items_type: str | None = None
    items_field: str | None = None
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
SESSION_QUERY_DEFAULT_LIMIT = 20
SESSION_QUERY_MAX_LIMIT = 100
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
            OPENCODE_DIRECTORY_METADATA_FIELD,
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
            OPENCODE_DIRECTORY_METADATA_FIELD,
        ),
        result_fields=("item",),
        notification_response_status=204,
    ),
    "shell": SessionQueryMethodContract(
        method="opencode.sessions.shell",
        required_params=("session_id", "request.agent", "request.command"),
        optional_params=("request.model", OPENCODE_DIRECTORY_METADATA_FIELD),
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

CORE_JSONRPC_METHODS: tuple[str, ...] = (
    "message/send",
    "message/stream",
    "tasks/get",
    "tasks/cancel",
    "tasks/resubscribe",
)
CORE_HTTP_ENDPOINTS: tuple[str, ...] = (
    "POST /v1/message:send",
    "POST /v1/message:stream",
    "GET /v1/tasks/{id}",
    "POST /v1/tasks/{id}:cancel",
    "GET /v1/tasks/{id}:subscribe",
)
WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "supported_methods",
    "protocol_version",
)

SESSION_QUERY_ERROR_BUSINESS_CODES: dict[str, int] = {
    "SESSION_NOT_FOUND": -32001,
    "SESSION_FORBIDDEN": -32006,
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
        method="a2a.interrupt.permission.reply",
        required_params=("request_id", "reply"),
        optional_params=("message", "metadata"),
        notification_response_status=204,
    ),
    "reply_question": InterruptMethodContract(
        method="a2a.interrupt.question.reply",
        required_params=("request_id", "answers"),
        optional_params=("metadata",),
        notification_response_status=204,
    ),
    "reject_question": InterruptMethodContract(
        method="a2a.interrupt.question.reject",
        required_params=("request_id",),
        optional_params=("metadata",),
        notification_response_status=204,
    ),
}

INTERRUPT_CALLBACK_METHODS: dict[str, str] = {
    key: contract.method for key, contract in INTERRUPT_CALLBACK_METHOD_CONTRACTS.items()
}

PROVIDER_DISCOVERY_METHOD_CONTRACTS: dict[str, ProviderDiscoveryMethodContract] = {
    "list_providers": ProviderDiscoveryMethodContract(
        method="opencode.providers.list",
        result_fields=("items", "default_by_provider", "connected"),
        items_type="ProviderSummary[]",
        items_field="items",
        notification_response_status=204,
    ),
    "list_models": ProviderDiscoveryMethodContract(
        method="opencode.models.list",
        optional_params=("provider_id",),
        result_fields=("items", "default_by_provider", "connected"),
        items_type="ModelSummary[]",
        items_field="items",
        notification_response_status=204,
    ),
}

PROVIDER_DISCOVERY_METHODS: dict[str, str] = {
    key: contract.method for key, contract in PROVIDER_DISCOVERY_METHOD_CONTRACTS.items()
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
PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES: dict[str, int] = {
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
    "UPSTREAM_PAYLOAD_ERROR": -32005,
}
PROVIDER_DISCOVERY_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "upstream_status",
    "detail",
)
PROVIDER_DISCOVERY_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
)


def build_supported_jsonrpc_methods(*, session_shell_enabled: bool) -> list[str]:
    methods = [
        *CORE_JSONRPC_METHODS,
        SESSION_QUERY_METHODS["list_sessions"],
        SESSION_QUERY_METHODS["get_session_messages"],
        SESSION_CONTROL_METHODS["prompt_async"],
        SESSION_CONTROL_METHODS["command"],
        *PROVIDER_DISCOVERY_METHODS.values(),
        *INTERRUPT_CALLBACK_METHODS.values(),
    ]
    if session_shell_enabled:
        methods.append(SESSION_CONTROL_METHODS["shell"])
    return methods


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


def build_session_binding_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    profile_metadata = build_contract_profile_metadata(runtime_profile)
    return {
        "metadata_field": SHARED_SESSION_BINDING_FIELD,
        "behavior": "prefer_metadata_binding_else_create_session",
        "supported_metadata": [
            "shared.session.id",
            "opencode.directory",
        ],
        "provider_private_metadata": ["opencode.directory"],
        "directory_override_enabled": runtime_profile.runtime_features.directory_override_enabled,
        **profile_metadata,
        "notes": [
            (
                "If metadata.shared.session.id is provided, the server will send the "
                "message to that upstream session."
            ),
            (
                "Otherwise, the server will create a new upstream session and cache "
                "the (identity, contextId)->session_id mapping in memory with TTL."
            ),
        ],
    }


def build_model_selection_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    profile_metadata = build_contract_profile_metadata(runtime_profile)
    return {
        "metadata_field": SHARED_MODEL_SELECTION_FIELD,
        "behavior": "prefer_metadata_model_else_upstream_default",
        "applies_to_methods": ["message/send", "message/stream"],
        "supported_metadata": [
            "shared.model.providerID",
            "shared.model.modelID",
        ],
        "provider_private_metadata": [],
        **profile_metadata,
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
            "details": f"{SHARED_INTERRUPT_METADATA_FIELD}.details",
        },
        "usage_fields": {
            "input_tokens": f"{SHARED_USAGE_METADATA_FIELD}.input_tokens",
            "output_tokens": f"{SHARED_USAGE_METADATA_FIELD}.output_tokens",
            "total_tokens": f"{SHARED_USAGE_METADATA_FIELD}.total_tokens",
            "cost": f"{SHARED_USAGE_METADATA_FIELD}.cost",
        },
    }


def build_session_query_extension_params(
    *,
    runtime_profile: RuntimeProfile,
    context_id_prefix: str,
) -> dict[str, Any]:
    profile_metadata = build_contract_profile_metadata(runtime_profile)
    session_shell_enabled = runtime_profile.runtime_features.session_shell_enabled
    methods = dict(SESSION_QUERY_METHODS)
    control_methods = dict(SESSION_CONTROL_METHODS)
    if not session_shell_enabled:
        methods.pop("shell", None)
        control_methods.pop("shell", None)

    method_contracts: dict[str, Any] = {}
    pagination_applies_to: list[str] = []

    for method_contract in SESSION_QUERY_METHOD_CONTRACTS.values():
        if method_contract.method == SESSION_QUERY_METHODS["shell"] and not session_shell_enabled:
            continue
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

        if method_contract.pagination_mode == SESSION_QUERY_PAGINATION_MODE:
            pagination_applies_to.append(method_contract.method)

    return {
        "methods": methods,
        "control_methods": control_methods,
        "control_method_flags": {
            SESSION_QUERY_METHODS["shell"]: {
                "enabled_by_default": False,
                "config_key": "A2A_ENABLE_SESSION_SHELL",
            }
        },
        **profile_metadata,
        "pagination": {
            "mode": SESSION_QUERY_PAGINATION_MODE,
            "default_limit": SESSION_QUERY_DEFAULT_LIMIT,
            "max_limit": SESSION_QUERY_MAX_LIMIT,
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
        "context_semantics": {
            "a2a_context_id_field": "contextId",
            "a2a_context_id_prefix": context_id_prefix,
            "upstream_session_id_field": SHARED_SESSION_BINDING_FIELD,
        },
    }


def build_interrupt_callback_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    profile_metadata = build_contract_profile_metadata(runtime_profile)
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
        "supported_metadata": ["opencode.directory"],
        "provider_private_metadata": ["opencode.directory"],
        "context_fields": {
            "directory": OPENCODE_DIRECTORY_METADATA_FIELD,
        },
        "success_result_fields": list(INTERRUPT_SUCCESS_RESULT_FIELDS),
        "errors": {
            "business_codes": dict(INTERRUPT_ERROR_BUSINESS_CODES),
            "error_types": list(INTERRUPT_ERROR_TYPES),
            "error_data_fields": list(INTERRUPT_ERROR_DATA_FIELDS),
            "invalid_params_data_fields": list(INTERRUPT_INVALID_PARAMS_DATA_FIELDS),
        },
        **profile_metadata,
    }


def build_provider_discovery_extension_params(
    *,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    profile_metadata = build_contract_profile_metadata(runtime_profile)
    method_contracts: dict[str, Any] = {}

    for method_contract in PROVIDER_DISCOVERY_METHOD_CONTRACTS.values():
        params_contract = _build_method_contract_params(
            required=method_contract.required_params,
            optional=method_contract.optional_params,
            unsupported=(),
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

    return {
        "methods": dict(PROVIDER_DISCOVERY_METHODS),
        "method_contracts": method_contracts,
        "supported_metadata": ["opencode.directory"],
        "provider_private_metadata": ["opencode.directory"],
        "context_fields": {
            "directory": OPENCODE_DIRECTORY_METADATA_FIELD,
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
        **profile_metadata,
        "notes": [
            (
                "Provider/model discovery is OpenCode-specific and exposed through "
                "provider-private JSON-RPC methods."
            ),
            (
                "The server normalizes upstream provider catalogs into summary records so "
                "downstream callers do not need to parse raw OpenCode payloads."
            ),
        ],
    }


def build_compatibility_profile_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    session_shell_enabled = runtime_profile.runtime_features.session_shell_enabled
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
                "extension_uri": SESSION_QUERY_EXTENSION_URI,
            }
            for method in (
                SESSION_QUERY_METHODS["list_sessions"],
                SESSION_QUERY_METHODS["get_session_messages"],
                SESSION_CONTROL_METHODS["prompt_async"],
                SESSION_CONTROL_METHODS["command"],
            )
        }
    )
    method_retention[SESSION_CONTROL_METHODS["shell"]] = {
        "surface": "extension",
        "availability": "enabled" if session_shell_enabled else "disabled",
        "retention": "deployment-conditional",
        "extension_uri": SESSION_QUERY_EXTENSION_URI,
        "toggle": "A2A_ENABLE_SESSION_SHELL",
    }
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
                "extension_uri": INTERRUPT_CALLBACK_EXTENSION_URI,
            }
            for method in INTERRUPT_CALLBACK_METHODS.values()
        }
    )
    return {
        **runtime_profile.to_public_dict(protocol_version=protocol_version),
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
            SESSION_QUERY_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
            },
            PROVIDER_DISCOVERY_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
            },
            INTERRUPT_CALLBACK_EXTENSION_URI: {
                "surface": "jsonrpc-extension",
                "availability": "always",
                "retention": "stable",
            },
        },
        "method_retention": method_retention,
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
                "Treat opencode.sessions.*, opencode.providers.*, and opencode.models.* as "
                "provider-private operational surfaces rather than portable A2A baseline "
                "capabilities."
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
        ],
    }


def build_wire_contract_params(
    *,
    protocol_version: str,
    runtime_profile: RuntimeProfile,
) -> dict[str, Any]:
    session_shell_enabled = runtime_profile.runtime_features.session_shell_enabled
    extension_jsonrpc_methods = [
        SESSION_QUERY_METHODS["list_sessions"],
        SESSION_QUERY_METHODS["get_session_messages"],
        SESSION_CONTROL_METHODS["prompt_async"],
        SESSION_CONTROL_METHODS["command"],
        *PROVIDER_DISCOVERY_METHODS.values(),
        *INTERRUPT_CALLBACK_METHODS.values(),
    ]
    conditionally_available_methods: dict[str, dict[str, str]] = {}
    if session_shell_enabled:
        extension_jsonrpc_methods.append(SESSION_CONTROL_METHODS["shell"])
    else:
        conditionally_available_methods[SESSION_CONTROL_METHODS["shell"]] = {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_SESSION_SHELL",
        }

    return {
        "protocol_version": protocol_version,
        "profile": runtime_profile.to_public_dict(protocol_version=protocol_version),
        "preferred_transport": "HTTP+JSON",
        "additional_transports": ["JSON-RPC"],
        "core": {
            "jsonrpc_methods": list(CORE_JSONRPC_METHODS),
            "http_endpoints": list(CORE_HTTP_ENDPOINTS),
        },
        "extensions": {
            "jsonrpc_methods": extension_jsonrpc_methods,
            "conditionally_available_methods": conditionally_available_methods,
            "extension_uris": [
                SESSION_BINDING_EXTENSION_URI,
                MODEL_SELECTION_EXTENSION_URI,
                STREAMING_EXTENSION_URI,
                SESSION_QUERY_EXTENSION_URI,
                PROVIDER_DISCOVERY_EXTENSION_URI,
                INTERRUPT_CALLBACK_EXTENSION_URI,
            ],
        },
        "all_jsonrpc_methods": build_supported_jsonrpc_methods(
            session_shell_enabled=session_shell_enabled,
        ),
        "unsupported_method_error": {
            "code": -32601,
            "type": "METHOD_NOT_SUPPORTED",
            "data_fields": list(WIRE_CONTRACT_UNSUPPORTED_METHOD_DATA_FIELDS),
        },
    }

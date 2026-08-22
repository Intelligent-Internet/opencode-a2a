from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...a2a_protocol import (
    CORE_JSONRPC_METHODS as DECLARED_CORE_JSONRPC_METHODS,
)
from ...a2a_protocol import (
    EXTENDED_AGENT_CARD_PATH,
)
from .identifiers import (
    OPENCODE_DIRECTORY_METADATA_FIELD,
    OPENCODE_WORKSPACE_METADATA_FIELD,
)


@dataclass(frozen=True)
class SessionQueryMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    unsupported_params: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    items_type: str | None = None
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
    notification_response_status: int | None = None


@dataclass(frozen=True)
class InterruptRecoveryMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    items_type: str | None = None
    notification_response_status: int | None = None


@dataclass(frozen=True)
class WorkspaceControlMethodContract:
    method: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    items_type: str | None = None
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
PROMPT_ASYNC_SUPPORTED_PART_TYPES: tuple[str, ...] = ("text", "file", "agent", "subtask")
PROMPT_ASYNC_PART_CONTRACTS: dict[str, dict[str, Any]] = {
    "text": {
        "required": ("type", "text"),
    },
    "file": {
        "required": ("type", "mime", "url"),
    },
    "agent": {
        "required": ("type", "name"),
    },
    "subtask": {
        "required": ("type", "prompt", "description", "agent"),
        "optional": ("model", "command"),
    },
}
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

SESSION_QUERY_PAGINATION_MODE = "limit_and_optional_cursor"
SESSION_QUERY_PAGINATION_BEHAVIOR = "passthrough"
SESSION_QUERY_DEFAULT_LIMIT = 20
SESSION_QUERY_MAX_LIMIT = 100
SESSION_QUERY_PAGINATION_PARAMS: tuple[str, ...] = ("limit", "before")
SESSION_QUERY_PAGINATION_UNSUPPORTED: tuple[str, ...] = ("cursor", "page", "size")

SESSION_METHOD_CONTRACTS: dict[str, SessionQueryMethodContract] = {
    "status": SessionQueryMethodContract(
        method="opencode.sessions.status",
        optional_params=("directory", OPENCODE_WORKSPACE_METADATA_FIELD),
        result_fields=("items",),
        items_type="SessionStatusSummary[]",
        notification_response_status=204,
    ),
    "list_sessions": SessionQueryMethodContract(
        method="opencode.sessions.list",
        optional_params=(
            "limit",
            "directory",
            OPENCODE_WORKSPACE_METADATA_FIELD,
            "roots",
            "start",
            "search",
        ),
        unsupported_params=SESSION_QUERY_PAGINATION_UNSUPPORTED,
        result_fields=("items",),
        items_type="Task[]",
        notification_response_status=204,
        pagination_mode=SESSION_QUERY_PAGINATION_MODE,
    ),
    "get_session_messages": SessionQueryMethodContract(
        method="opencode.sessions.messages.list",
        required_params=("session_id",),
        optional_params=(
            "limit",
            "before",
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        unsupported_params=SESSION_QUERY_PAGINATION_UNSUPPORTED,
        result_fields=("items", "next_cursor"),
        items_type="Message[]",
        notification_response_status=204,
        pagination_mode=SESSION_QUERY_PAGINATION_MODE,
    ),
    "get_session": SessionQueryMethodContract(
        method="opencode.sessions.get",
        required_params=("session_id",),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="Task",
        notification_response_status=204,
    ),
    "get_session_children": SessionQueryMethodContract(
        method="opencode.sessions.children",
        required_params=("session_id",),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("items",),
        items_type="Task[]",
        notification_response_status=204,
    ),
    "get_session_todo": SessionQueryMethodContract(
        method="opencode.sessions.todo",
        required_params=("session_id",),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("items",),
        items_type="Todo[]",
        notification_response_status=204,
    ),
    "get_session_diff": SessionQueryMethodContract(
        method="opencode.sessions.diff",
        required_params=("session_id",),
        optional_params=(
            "message_id",
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("items",),
        items_type="FileDiff[]",
        notification_response_status=204,
    ),
    "get_session_message": SessionQueryMethodContract(
        method="opencode.sessions.messages.get",
        required_params=("session_id", "message_id"),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="Message",
        notification_response_status=204,
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
            OPENCODE_WORKSPACE_METADATA_FIELD,
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
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        notification_response_status=204,
    ),
    "fork": SessionQueryMethodContract(
        method="opencode.sessions.fork",
        required_params=("session_id",),
        optional_params=(
            "request.messageID",
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="SessionSummary",
        notification_response_status=204,
    ),
    "share": SessionQueryMethodContract(
        method="opencode.sessions.share",
        required_params=("session_id",),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="SessionSummary",
        notification_response_status=204,
    ),
    "unshare": SessionQueryMethodContract(
        method="opencode.sessions.unshare",
        required_params=("session_id",),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="SessionSummary",
        notification_response_status=204,
    ),
    "summarize": SessionQueryMethodContract(
        method="opencode.sessions.summarize",
        required_params=("session_id",),
        optional_params=(
            "request.providerID",
            "request.modelID",
            "request.auto",
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("ok", "session_id"),
        notification_response_status=204,
    ),
    "revert": SessionQueryMethodContract(
        method="opencode.sessions.revert",
        required_params=("session_id", "request.messageID"),
        optional_params=(
            "request.partID",
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="SessionSummary",
        notification_response_status=204,
    ),
    "unrevert": SessionQueryMethodContract(
        method="opencode.sessions.unrevert",
        required_params=("session_id",),
        optional_params=(
            "directory",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        items_type="SessionSummary",
        notification_response_status=204,
    ),
    "shell": SessionQueryMethodContract(
        method="opencode.sessions.shell",
        required_params=("session_id", "request.agent", "request.command"),
        optional_params=(
            "request.model",
            OPENCODE_DIRECTORY_METADATA_FIELD,
            OPENCODE_WORKSPACE_METADATA_FIELD,
        ),
        result_fields=("item",),
        notification_response_status=204,
    ),
}

SESSION_METHODS: dict[str, str] = {
    key: contract.method for key, contract in SESSION_METHOD_CONTRACTS.items()
}
SESSION_CONTROL_METHOD_KEYS: tuple[str, ...] = ("prompt_async", "command", "shell")
SESSION_CONTROL_METHODS: dict[str, str] = {
    key: SESSION_METHODS[key] for key in SESSION_CONTROL_METHOD_KEYS
}
SESSION_READ_METHOD_KEYS: tuple[str, ...] = (
    "status",
    "list_sessions",
    "get_session",
    "get_session_children",
    "get_session_todo",
    "get_session_diff",
    "get_session_message",
    "get_session_messages",
)
SESSION_READ_METHODS: dict[str, str] = {
    key: SESSION_METHODS[key] for key in SESSION_READ_METHOD_KEYS
}
SESSION_MUTATION_METHOD_KEYS: tuple[str, ...] = (
    "fork",
    "share",
    "unshare",
    "summarize",
    "revert",
    "unrevert",
)
SESSION_MUTATION_METHODS: dict[str, str] = {
    key: SESSION_METHODS[key] for key in SESSION_MUTATION_METHOD_KEYS
}

CORE_JSONRPC_METHODS: tuple[str, ...] = tuple(DECLARED_CORE_JSONRPC_METHODS)
CORE_HTTP_ENDPOINTS: tuple[str, ...] = (
    "POST /message:send",
    "POST /message:stream",
    "GET /tasks",
    "GET /tasks/{id}",
    "POST /tasks/{id}:cancel",
    "GET /tasks/{id}:subscribe",
    "GET /tasks/{id}/pushNotificationConfigs",
    "POST /tasks/{id}/pushNotificationConfigs",
    "GET /tasks/{id}/pushNotificationConfigs/{push_id}",
    f"GET {EXTENDED_AGENT_CARD_PATH}",
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
    "AUTHORIZATION_FORBIDDEN": -32007,
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
    "UPSTREAM_PAYLOAD_ERROR": -32005,
}
SESSION_QUERY_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "session_id",
    "capability",
    "credential_id",
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
        notification_response_status=204,
    ),
    "list_models": ProviderDiscoveryMethodContract(
        method="opencode.models.list",
        optional_params=("provider_id",),
        result_fields=("items", "default_by_provider", "connected"),
        items_type="ModelSummary[]",
        notification_response_status=204,
    ),
}

PROVIDER_DISCOVERY_METHODS: dict[str, str] = {
    key: contract.method for key, contract in PROVIDER_DISCOVERY_METHOD_CONTRACTS.items()
}

INTERRUPT_RECOVERY_METHOD_CONTRACTS: dict[str, InterruptRecoveryMethodContract] = {
    "list_permissions": InterruptRecoveryMethodContract(
        method="opencode.permissions.list",
        result_fields=("items",),
        items_type="InterruptRequest[]",
        notification_response_status=204,
    ),
    "list_questions": InterruptRecoveryMethodContract(
        method="opencode.questions.list",
        result_fields=("items",),
        items_type="InterruptRequest[]",
        notification_response_status=204,
    ),
}

INTERRUPT_RECOVERY_METHODS: dict[str, str] = {
    key: contract.method for key, contract in INTERRUPT_RECOVERY_METHOD_CONTRACTS.items()
}

WORKSPACE_CONTROL_METHOD_CONTRACTS: dict[str, WorkspaceControlMethodContract] = {
    "list_projects": WorkspaceControlMethodContract(
        method="opencode.projects.list",
        result_fields=("items",),
        items_type="Project[]",
        notification_response_status=204,
    ),
    "get_current_project": WorkspaceControlMethodContract(
        method="opencode.projects.current",
        result_fields=("item",),
        items_type="Project",
        notification_response_status=204,
    ),
    "list_workspaces": WorkspaceControlMethodContract(
        method="opencode.workspaces.list",
        result_fields=("items",),
        items_type="Workspace[]",
        notification_response_status=204,
    ),
    "create_workspace": WorkspaceControlMethodContract(
        method="opencode.workspaces.create",
        required_params=("request.type",),
        optional_params=("request.id", "request.branch", "request.extra"),
        result_fields=("item",),
        items_type="Workspace",
        notification_response_status=204,
    ),
    "remove_workspace": WorkspaceControlMethodContract(
        method="opencode.workspaces.remove",
        required_params=("workspace_id",),
        result_fields=("item",),
        items_type="Workspace|null",
        notification_response_status=204,
    ),
    "list_worktrees": WorkspaceControlMethodContract(
        method="opencode.worktrees.list",
        result_fields=("items",),
        items_type="string[]",
        notification_response_status=204,
    ),
    "create_worktree": WorkspaceControlMethodContract(
        method="opencode.worktrees.create",
        optional_params=("request.name", "request.startCommand"),
        result_fields=("item",),
        items_type="Worktree",
        notification_response_status=204,
    ),
    "remove_worktree": WorkspaceControlMethodContract(
        method="opencode.worktrees.remove",
        required_params=("request.directory",),
        result_fields=("ok",),
        items_type="boolean",
        notification_response_status=204,
    ),
    "reset_worktree": WorkspaceControlMethodContract(
        method="opencode.worktrees.reset",
        required_params=("request.directory",),
        result_fields=("ok",),
        items_type="boolean",
        notification_response_status=204,
    ),
}

WORKSPACE_CONTROL_METHODS: dict[str, str] = {
    key: contract.method for key, contract in WORKSPACE_CONTROL_METHOD_CONTRACTS.items()
}
WORKSPACE_DISCOVERY_METHOD_KEYS: tuple[str, ...] = (
    "list_projects",
    "get_current_project",
    "list_workspaces",
    "list_worktrees",
)
WORKSPACE_DISCOVERY_METHODS: dict[str, str] = {
    key: WORKSPACE_CONTROL_METHODS[key] for key in WORKSPACE_DISCOVERY_METHOD_KEYS
}
WORKSPACE_STABLE_METHOD_KEYS: tuple[str, ...] = ("list_projects", "get_current_project")
WORKSPACE_STABLE_METHODS: dict[str, str] = {
    key: WORKSPACE_CONTROL_METHODS[key] for key in WORKSPACE_STABLE_METHOD_KEYS
}
WORKSPACE_EXPERIMENTAL_UPSTREAM_METHOD_KEYS: tuple[str, ...] = (
    "list_workspaces",
    "list_worktrees",
    "create_workspace",
    "remove_workspace",
    "create_worktree",
    "remove_worktree",
    "reset_worktree",
)
WORKSPACE_EXPERIMENTAL_UPSTREAM_METHODS: dict[str, str] = {
    key: WORKSPACE_CONTROL_METHODS[key] for key in WORKSPACE_EXPERIMENTAL_UPSTREAM_METHOD_KEYS
}
WORKSPACE_MUTATION_METHOD_KEYS: tuple[str, ...] = (
    "create_workspace",
    "remove_workspace",
    "create_worktree",
    "remove_worktree",
    "reset_worktree",
)
WORKSPACE_MUTATION_METHODS: dict[str, str] = {
    key: WORKSPACE_CONTROL_METHODS[key] for key in WORKSPACE_MUTATION_METHOD_KEYS
}

INTERRUPT_SUCCESS_RESULT_FIELDS: tuple[str, ...] = ("ok", "request_id")
INTERRUPT_ERROR_BUSINESS_CODES: dict[str, int] = {
    "INTERRUPT_REQUEST_NOT_FOUND": -32004,
    "INTERRUPT_REQUEST_EXPIRED": -32007,
    "INTERRUPT_TYPE_MISMATCH": -32008,
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
INTERRUPT_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "request_id",
    "expected_interrupt_type",
    "actual_interrupt_type",
    "upstream_status",
    "detail",
)
INTERRUPT_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
    "request_id",
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
INTERRUPT_RECOVERY_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
)
WORKSPACE_CONTROL_ERROR_BUSINESS_CODES: dict[str, int] = {
    "AUTHORIZATION_FORBIDDEN": -32007,
    "UPSTREAM_UNREACHABLE": -32002,
    "UPSTREAM_HTTP_ERROR": -32003,
    "UPSTREAM_PAYLOAD_ERROR": -32005,
}
WORKSPACE_CONTROL_ERROR_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "method",
    "capability",
    "credential_id",
    "upstream_status",
    "detail",
)
WORKSPACE_CONTROL_INVALID_PARAMS_DATA_FIELDS: tuple[str, ...] = (
    "type",
    "field",
    "fields",
)

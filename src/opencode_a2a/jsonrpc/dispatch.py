from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeAlias

from a2a.utils.errors import A2AError
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from ..a2a_protocol import CORE_JSONRPC_METHODS as DECLARED_CORE_JSONRPC_METHODS
from ..contracts.extensions import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
)
from ..opencode_upstream_client import OpencodeUpstreamClient
from .models import JSONRPCError, JSONRPCRequest

# Delegate all SDK-owned JSON-RPC methods to the base app, then let the local
# extension registry override only the OpenCode-specific methods.
CORE_JSONRPC_METHODS = frozenset(DECLARED_CORE_JSONRPC_METHODS)

ErrorResponseFactory: TypeAlias = Callable[[str | int | None, JSONRPCError | A2AError], Response]
SuccessResponseFactory: TypeAlias = Callable[[str | int, Any], JSONResponse]
SessionClaimFunc: TypeAlias = Callable[..., Awaitable[bool]]
SessionFinalizeFunc: TypeAlias = Callable[..., Awaitable[None]]
SessionReleaseFunc: TypeAlias = Callable[..., Awaitable[None]]
ExtensionHandlerFunc: TypeAlias = Callable[
    ["ExtensionHandlerContext", JSONRPCRequest, dict[str, Any], Request],
    Awaitable[Response],
]


@dataclass(frozen=True)
class ExtensionHandlerContext:
    upstream_client: OpencodeUpstreamClient
    method_session_status: str
    method_list_sessions: str
    method_get_session: str
    method_get_session_children: str
    method_get_session_todo: str
    method_get_session_diff: str
    method_get_session_message: str
    method_get_session_messages: str
    method_prompt_async: str
    method_command: str
    method_fork_session: str
    method_share_session: str
    method_unshare_session: str
    method_summarize_session: str
    method_revert_session: str
    method_unrevert_session: str
    method_shell: str | None
    method_list_providers: str
    method_list_models: str
    method_list_projects: str
    method_get_current_project: str
    method_list_workspaces: str
    method_create_workspace: str | None
    method_remove_workspace: str | None
    method_list_worktrees: str
    method_create_worktree: str | None
    method_remove_worktree: str | None
    method_reset_worktree: str | None
    method_list_permissions: str
    method_list_questions: str
    method_reply_permission: str
    method_reply_question: str
    method_reject_question: str
    supported_methods: tuple[str, ...]
    directory_resolver: Callable[[str | None], str | None]
    session_claim: SessionClaimFunc
    session_claim_finalize: SessionFinalizeFunc
    session_claim_release: SessionReleaseFunc
    error_response: ErrorResponseFactory
    success_response: SuccessResponseFactory


@dataclass(frozen=True)
class ExtensionMethodSpec:
    name: str
    methods: frozenset[str]
    extension_uri: str
    handler: ExtensionHandlerFunc


class ExtensionMethodRegistry:
    def __init__(self, specs: Iterable[ExtensionMethodSpec]) -> None:
        method_map: dict[str, ExtensionMethodSpec] = {}
        normalized_specs: list[ExtensionMethodSpec] = []
        for spec in specs:
            normalized_specs.append(spec)
            for method in spec.methods:
                existing = method_map.get(method)
                if existing is not None:
                    raise ValueError(
                        f"Extension method {method!r} registered by both "
                        f"{existing.name!r} and {spec.name!r}"
                    )
                method_map[method] = spec
        self._specs = tuple(normalized_specs)
        self._method_map = method_map

    @property
    def specs(self) -> tuple[ExtensionMethodSpec, ...]:
        return self._specs

    def methods(self) -> frozenset[str]:
        return frozenset(self._method_map)

    def resolve(self, method: str) -> ExtensionMethodSpec | None:
        return self._method_map.get(method)


def build_extension_method_registry(
    context: ExtensionHandlerContext,
) -> ExtensionMethodRegistry:
    from .handlers.interrupt_callbacks import handle_interrupt_callback_request
    from .handlers.interrupt_queries import handle_interrupt_query_request
    from .handlers.provider_discovery import handle_provider_discovery_request
    from .handlers.session_control import handle_session_control_request
    from .handlers.session_lifecycle import handle_session_lifecycle_request
    from .handlers.session_queries import handle_session_query_request
    from .handlers.workspace_control import handle_workspace_control_request

    session_control_methods = {context.method_prompt_async, context.method_command}
    if context.method_shell is not None:
        session_control_methods.add(context.method_shell)
    session_item_methods = {
        context.method_session_status,
        context.method_get_session,
        context.method_get_session_children,
        context.method_get_session_todo,
        context.method_get_session_diff,
        context.method_get_session_message,
        context.method_fork_session,
        context.method_share_session,
        context.method_unshare_session,
        context.method_summarize_session,
        context.method_revert_session,
        context.method_unrevert_session,
    }

    workspace_control_methods = {
        context.method_list_projects,
        context.method_get_current_project,
        context.method_list_workspaces,
        context.method_list_worktrees,
    }
    for method in (
        context.method_create_workspace,
        context.method_remove_workspace,
        context.method_create_worktree,
        context.method_remove_worktree,
        context.method_reset_worktree,
    ):
        if method is not None:
            workspace_control_methods.add(method)

    return ExtensionMethodRegistry(
        (
            ExtensionMethodSpec(
                name="session_lifecycle",
                methods=frozenset(session_item_methods),
                extension_uri=SESSION_MANAGEMENT_EXTENSION_URI,
                handler=handle_session_lifecycle_request,
            ),
            ExtensionMethodSpec(
                name="session_listing",
                methods=frozenset(
                    {
                        context.method_list_sessions,
                        context.method_get_session_messages,
                    }
                ),
                extension_uri=SESSION_MANAGEMENT_EXTENSION_URI,
                handler=handle_session_query_request,
            ),
            ExtensionMethodSpec(
                name="provider_discovery",
                methods=frozenset(
                    {
                        context.method_list_providers,
                        context.method_list_models,
                    }
                ),
                extension_uri=PROVIDER_DISCOVERY_EXTENSION_URI,
                handler=handle_provider_discovery_request,
            ),
            ExtensionMethodSpec(
                name="interrupt_query",
                methods=frozenset(
                    {
                        context.method_list_permissions,
                        context.method_list_questions,
                    }
                ),
                extension_uri=INTERRUPT_RECOVERY_EXTENSION_URI,
                handler=handle_interrupt_query_request,
            ),
            ExtensionMethodSpec(
                name="workspace_control",
                methods=frozenset(workspace_control_methods),
                extension_uri=WORKSPACE_CONTROL_EXTENSION_URI,
                handler=handle_workspace_control_request,
            ),
            ExtensionMethodSpec(
                name="session_control",
                methods=frozenset(session_control_methods),
                extension_uri=SESSION_MANAGEMENT_EXTENSION_URI,
                handler=handle_session_control_request,
            ),
            ExtensionMethodSpec(
                name="interrupt_callback",
                methods=frozenset(
                    {
                        context.method_reply_permission,
                        context.method_reply_question,
                        context.method_reject_question,
                    }
                ),
                extension_uri=INTERRUPT_CALLBACK_EXTENSION_URI,
                handler=handle_interrupt_callback_request,
            ),
        )
    )

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import replace
from typing import Any, cast

from a2a.server.events import Event
from a2a.server.request_handlers.response_helpers import agent_card_to_dict, build_error_response
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.utils import proto_utils
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP, A2AError, UnsupportedOperationError
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict, ParseDict
from jsonrpc.jsonrpc2 import JSONRPC20Response
from starlette.requests import Request
from starlette.responses import Response

from ..contracts.extensions import ALL_EXTENSION_URIS
from ..extension_negotiation import (
    requested_extensions_from_call_context,
)
from ..opencode_upstream_client import OpencodeUpstreamClient
from ..server.runtime_limits import apply_stream_budget
from .dispatch import (
    ExtensionHandlerContext,
    build_extension_method_registry,
)
from .error_responses import (
    adapt_jsonrpc_error,
    invalid_params_error,
    method_not_supported_error,
)
from .models import JSONRPCError, JSONRPCRequest

logger = logging.getLogger(__name__)
_PUSH_NOTIFICATION_METHODS = frozenset(
    {
        "CreateTaskPushNotificationConfig",
        "DeleteTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
    }
)


class OpencodeSessionManagementJSONRPCApplication(JsonRpcDispatcher):
    """Dispatch OpenCode extension methods on top of the SDK JSON-RPC surface."""

    def __init__(
        self,
        *,
        http_handler,
        upstream_client: OpencodeUpstreamClient,
        methods: dict[str, str],
        supported_methods: list[str],
        directory_resolver: Callable[[str | None], str | None] | None = None,
        session_claim: Callable[..., Awaitable[bool]] | None = None,
        session_claim_finalize: Callable[..., Awaitable[None]] | None = None,
        session_claim_release: Callable[..., Awaitable[None]] | None = None,
        stream_budget_max_bytes: int = 0,
        stream_budget_max_duration_seconds: float = 0.0,
        stream_budget_idle_timeout_seconds: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(request_handler=http_handler, **kwargs)
        self._http_handler = http_handler
        self._upstream_client = upstream_client
        self._method_session_status = methods["status"]
        self._method_list_sessions = methods["list_sessions"]
        self._method_get_session = methods["get_session"]
        self._method_get_session_children = methods["get_session_children"]
        self._method_get_session_todo = methods["get_session_todo"]
        self._method_get_session_diff = methods["get_session_diff"]
        self._method_get_session_message = methods["get_session_message"]
        self._method_get_session_messages = methods["get_session_messages"]
        self._method_prompt_async = methods["prompt_async"]
        self._method_command = methods["command"]
        self._method_fork_session = methods["fork"]
        self._method_share_session = methods["share"]
        self._method_unshare_session = methods["unshare"]
        self._method_summarize_session = methods["summarize"]
        self._method_revert_session = methods["revert"]
        self._method_unrevert_session = methods["unrevert"]
        self._method_shell = methods.get("shell")
        self._method_list_providers = methods["list_providers"]
        self._method_list_models = methods["list_models"]
        self._method_list_projects = methods["list_projects"]
        self._method_get_current_project = methods["get_current_project"]
        self._method_list_workspaces = methods["list_workspaces"]
        self._method_create_workspace = methods.get("create_workspace")
        self._method_remove_workspace = methods.get("remove_workspace")
        self._method_list_worktrees = methods["list_worktrees"]
        self._method_create_worktree = methods.get("create_worktree")
        self._method_remove_worktree = methods.get("remove_worktree")
        self._method_reset_worktree = methods.get("reset_worktree")
        self._method_list_permissions = methods["list_permissions"]
        self._method_list_questions = methods["list_questions"]
        self._method_reply_permission = methods["reply_permission"]
        self._method_reply_question = methods["reply_question"]
        self._method_reject_question = methods["reject_question"]
        self._supported_methods = list(supported_methods)
        missing_control_hooks = [
            name
            for name, hook in (
                ("directory_resolver", directory_resolver),
                ("session_claim", session_claim),
                ("session_claim_finalize", session_claim_finalize),
                ("session_claim_release", session_claim_release),
            )
            if hook is None
        ]
        if missing_control_hooks:
            raise ValueError(
                "Control methods require guard hooks: " + ", ".join(sorted(missing_control_hooks))
            )
        self._directory_resolver = cast(Callable[[str | None], str | None], directory_resolver)
        self._session_claim = cast(Callable[..., Awaitable[bool]], session_claim)
        self._session_claim_finalize = cast(Callable[..., Awaitable[None]], session_claim_finalize)
        self._session_claim_release = cast(Callable[..., Awaitable[None]], session_claim_release)
        self._stream_budget_max_bytes = stream_budget_max_bytes
        self._stream_budget_max_duration_seconds = stream_budget_max_duration_seconds
        self._stream_budget_idle_timeout_seconds = stream_budget_idle_timeout_seconds
        self._extension_handler_context = ExtensionHandlerContext(
            upstream_client=self._upstream_client,
            method_session_status=self._method_session_status,
            method_list_sessions=self._method_list_sessions,
            method_get_session=self._method_get_session,
            method_get_session_children=self._method_get_session_children,
            method_get_session_todo=self._method_get_session_todo,
            method_get_session_diff=self._method_get_session_diff,
            method_get_session_message=self._method_get_session_message,
            method_get_session_messages=self._method_get_session_messages,
            method_prompt_async=self._method_prompt_async,
            method_command=self._method_command,
            method_fork_session=self._method_fork_session,
            method_share_session=self._method_share_session,
            method_unshare_session=self._method_unshare_session,
            method_summarize_session=self._method_summarize_session,
            method_revert_session=self._method_revert_session,
            method_unrevert_session=self._method_unrevert_session,
            method_shell=self._method_shell,
            method_list_providers=self._method_list_providers,
            method_list_models=self._method_list_models,
            method_list_projects=self._method_list_projects,
            method_get_current_project=self._method_get_current_project,
            method_list_workspaces=self._method_list_workspaces,
            method_create_workspace=self._method_create_workspace,
            method_remove_workspace=self._method_remove_workspace,
            method_list_worktrees=self._method_list_worktrees,
            method_create_worktree=self._method_create_worktree,
            method_remove_worktree=self._method_remove_worktree,
            method_reset_worktree=self._method_reset_worktree,
            method_list_permissions=self._method_list_permissions,
            method_list_questions=self._method_list_questions,
            method_reply_permission=self._method_reply_permission,
            method_reply_question=self._method_reply_question,
            method_reject_question=self._method_reject_question,
            supported_methods=tuple(self._supported_methods),
            directory_resolver=self._directory_resolver,
            session_claim=self._session_claim,
            session_claim_finalize=self._session_claim_finalize,
            session_claim_release=self._session_claim_release,
            error_response=cast(
                Callable[[str | int | None, JSONRPCError | A2AError], Response],
                self._generate_error_response,
            ),
            success_response=lambda request_id, result: JSONResponse(
                {"jsonrpc": "2.0", "id": request_id, "result": result}
            ),
        )
        self._extension_method_registry = build_extension_method_registry(
            self._extension_handler_context
        )

    def add_routes_to_app(self, app: FastAPI, *, rpc_url: str = "/") -> None:
        app.add_api_route(rpc_url, self.handle_requests, methods=["POST"])

    def _generate_protocol_error_response(
        self,
        request_id: str | int | None,
        error: JSONRPCError | A2AError,
    ) -> JSONResponse:
        adapted = adapt_jsonrpc_error(error)
        if isinstance(adapted, A2AError):
            error_payload = {
                "code": JSON_RPC_ERROR_CODE_MAP.get(type(adapted), -32603),
                "message": adapted.message,
            }
            if adapted.data is not None:
                error_payload["data"] = adapted.data
        else:
            error_payload = {
                "code": adapted.code,
                "message": adapted.message,
            }
            if adapted.data is not None:
                error_payload["data"] = adapted.data
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": error_payload},
            status_code=200,
        )

    async def _process_non_streaming_request(  # noqa: PLR0911
        self,
        request_obj: Any,
        context,
    ) -> dict[str, Any] | None:
        method = context.state.get("method")
        match method:
            case "SendMessage":
                return await self._handle_send_message(request_obj, context)
            case "CancelTask":
                return await self._handle_cancel_task(request_obj, context)
            case "GetTask":
                return await self._handle_get_task(request_obj, context)
            case "ListTasks":
                return await self._handle_list_tasks(request_obj, context)
            case "CreateTaskPushNotificationConfig":
                return await self._handle_create_task_push_notification_config(request_obj, context)
            case "GetTaskPushNotificationConfig":
                return await self._handle_get_task_push_notification_config(request_obj, context)
            case "ListTaskPushNotificationConfigs":
                return await self._handle_list_task_push_notification_configs(request_obj, context)
            case "DeleteTaskPushNotificationConfig":
                await self._handle_delete_task_push_notification_config(request_obj, context)
                return None
            case "GetExtendedAgentCard":
                return await self._handle_get_extended_agent_card(request_obj, context)
            case _:
                logger.error("Unhandled method: %s", method)
                raise UnsupportedOperationError(message=f"Method {method} is not supported.")

    async def _process_streaming_request(
        self,
        request_id: str | int | None,
        request_obj: Any,
        context,
    ) -> AsyncGenerator[dict[str, Any], None]:
        stream: AsyncGenerator | None = None
        method = context.state.get("method")
        if method == "SendStreamingMessage":
            stream = self.request_handler.on_message_send_stream(request_obj, context)
        elif method == "SubscribeToTask":
            stream = self.request_handler.on_subscribe_to_task(request_obj, context)

        if stream is None:
            raise UnsupportedOperationError(message="Stream not supported")

        try:
            first_event = await anext(stream)
        except StopAsyncIteration:
            first_event = None

        async def _wrap_stream(
            st: AsyncGenerator,
            first_evt: Event | None,
        ) -> AsyncGenerator[dict[str, Any], None]:
            def _map_event(evt: Event) -> dict[str, Any]:
                stream_response = proto_utils.to_stream_response(evt)
                result = MessageToDict(stream_response, preserving_proto_field_name=False)
                return cast(dict[str, Any], JSONRPC20Response(result=result, _id=request_id).data)

            try:
                if first_evt is not None:
                    yield _map_event(first_evt)

                async for event in st:
                    yield _map_event(event)
            except A2AError as error:
                yield build_error_response(request_id, error)

        return apply_stream_budget(
            _wrap_stream(stream, first_event),
            max_bytes=self._stream_budget_max_bytes,
            max_duration_seconds=self._stream_budget_max_duration_seconds,
            idle_timeout_seconds=self._stream_budget_idle_timeout_seconds,
        )

    async def _handle_core_request(
        self,
        request: Request,
        body: dict[str, Any],
        base_request: JSONRPCRequest,
    ) -> Response:
        canonical_method = base_request.method
        if canonical_method in _PUSH_NOTIFICATION_METHODS:
            return self._generate_protocol_error_response(
                base_request.id,
                UnsupportedOperationError(),
            )
        if canonical_method == "GetExtendedAgentCard":
            if base_request.id is None:
                return Response(status_code=204)
            extended_agent_card = getattr(self._http_handler, "extended_agent_card", None)
            if extended_agent_card is None:
                return self._generate_protocol_error_response(
                    base_request.id,
                    UnsupportedOperationError(
                        message="The agent does not support authenticated extended cards"
                    ),
                )
            requested_extensions = requested_extensions_from_call_context(
                self._context_builder.build(request)
            )
            request.state.activated_extensions = tuple(
                value for value in requested_extensions if value in ALL_EXTENSION_URIS
            )
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": base_request.id,
                    "result": agent_card_to_dict(extended_agent_card),
                }
            )
        model_class = self.METHOD_TO_MODEL.get(canonical_method)
        if model_class is None:
            if base_request.id is None:
                return Response(status_code=204)
            return self._generate_protocol_error_response(
                base_request.id,
                method_not_supported_error(
                    method=base_request.method,
                    supported_methods=self._supported_methods,
                ),
            )

        try:
            params = body.get("params", {})
            specific_request = ParseDict(params, model_class())
        except Exception as exc:
            return self._generate_protocol_error_response(
                base_request.id,
                invalid_params_error(str(exc)),
            )

        call_context = self._context_builder.build(request)
        call_context.tenant = getattr(specific_request, "tenant", "")
        call_context.state["method"] = canonical_method
        call_context.state["request_id"] = base_request.id
        try:
            if canonical_method in {"SendStreamingMessage", "SubscribeToTask"}:
                handler_result = await self._process_streaming_request(
                    base_request.id,
                    specific_request,
                    call_context,
                )
                return self._create_response(call_context, handler_result)

            raw_result = await self._process_non_streaming_request(specific_request, call_context)
            if base_request.id is None:
                return Response(status_code=204)
            return JSONResponse({"jsonrpc": "2.0", "id": base_request.id, "result": raw_result})
        except A2AError as exc:
            return self._generate_protocol_error_response(
                base_request.id,
                exc,
            )

    async def handle_requests(self, request: Request) -> Response:
        request_id: str | int | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get("id")
                if request_id is not None and not isinstance(request_id, str | int):
                    request_id = None
            base_request = JSONRPCRequest.model_validate(body)
        except Exception:
            return await super().handle_requests(request)

        extension_spec = self._extension_method_registry.resolve(base_request.method)
        if extension_spec is None:
            return await self._handle_core_request(
                request,
                body,
                base_request,
            )

        call_context = self._context_builder.build(request)
        requested_extensions = requested_extensions_from_call_context(call_context)
        if extension_spec.extension_uri not in requested_extensions:
            return self._generate_protocol_error_response(
                base_request.id,
                UnsupportedOperationError(
                    message=(
                        f"Method {base_request.method} requires explicit A2A extension "
                        "negotiation via the A2A-Extensions header."
                    ),
                    data={
                        "type": "EXTENSION_NEGOTIATION_REQUIRED",
                        "method": base_request.method,
                        "required_extensions": [extension_spec.extension_uri],
                        "requested_extensions": sorted(requested_extensions),
                        "header": "A2A-Extensions",
                    },
                ),
            )

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_protocol_error_response(
                base_request.id,
                invalid_params_error("params must be an object"),
            )
        request_context = replace(
            self._extension_handler_context,
            error_response=self._generate_protocol_error_response,
        )
        request.state.activated_extensions = (extension_spec.extension_uri,)
        return await extension_spec.handler(
            request_context,
            base_request,
            params,
            request,
        )

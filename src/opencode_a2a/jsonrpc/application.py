from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import (
    A2AError,
    InvalidRequestError,
    JSONRPCRequest,
)
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from ..opencode_upstream_client import OpencodeUpstreamClient
from .dispatch import (
    CORE_JSONRPC_METHODS,
    ExtensionHandlerContext,
    build_extension_method_registry,
)
from .error_responses import (
    invalid_params_error,
    method_not_supported_error,
)
from .methods import (
    SESSION_CONTEXT_PREFIX,
    _extract_provider_catalog,
    _normalize_model_summaries,
    _normalize_permission_reply,
    _normalize_provider_summaries,
    _parse_question_answers,
    _PromptAsyncValidationError,
    _validate_command_request_payload,
    _validate_prompt_async_format,
    _validate_prompt_async_part,
    _validate_shell_request_payload,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SESSION_CONTEXT_PREFIX",
    "_extract_provider_catalog",
    "_normalize_model_summaries",
    "_normalize_permission_reply",
    "_normalize_provider_summaries",
    "_parse_question_answers",
    "_PromptAsyncValidationError",
    "_validate_command_request_payload",
    "_validate_prompt_async_format",
    "_validate_prompt_async_part",
    "_validate_shell_request_payload",
]


class OpencodeSessionQueryJSONRPCApplication(A2AFastAPIApplication):
    """Extend A2A JSON-RPC endpoint with OpenCode session methods.

    These methods are optional (declared via AgentCard.capabilities.extensions) and do
    not require additional private REST endpoints.
    """

    def __init__(
        self,
        *args: Any,
        upstream_client: OpencodeUpstreamClient,
        methods: dict[str, str],
        protocol_version: str,
        supported_methods: list[str],
        directory_resolver: Callable[[str | None], str | None] | None = None,
        session_claim: Callable[..., Awaitable[bool]] | None = None,
        session_claim_finalize: Callable[..., Awaitable[None]] | None = None,
        session_claim_release: Callable[..., Awaitable[None]] | None = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._upstream_client = upstream_client
        self._method_list_sessions = methods["list_sessions"]
        self._method_get_session_messages = methods["get_session_messages"]
        self._method_prompt_async = methods["prompt_async"]
        self._method_command = methods["command"]
        self._method_shell = methods.get("shell")
        self._method_list_providers = methods["list_providers"]
        self._method_list_models = methods["list_models"]
        self._method_list_permissions = methods["list_permissions"]
        self._method_list_questions = methods["list_questions"]
        self._method_reply_permission = methods["reply_permission"]
        self._method_reply_question = methods["reply_question"]
        self._method_reject_question = methods["reject_question"]
        self._protocol_version = protocol_version
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
        self._extension_handler_context = ExtensionHandlerContext(
            upstream_client=self._upstream_client,
            method_list_sessions=self._method_list_sessions,
            method_get_session_messages=self._method_get_session_messages,
            method_prompt_async=self._method_prompt_async,
            method_command=self._method_command,
            method_shell=self._method_shell,
            method_list_providers=self._method_list_providers,
            method_list_models=self._method_list_models,
            method_list_permissions=self._method_list_permissions,
            method_list_questions=self._method_list_questions,
            method_reply_permission=self._method_reply_permission,
            method_reply_question=self._method_reply_question,
            method_reject_question=self._method_reject_question,
            protocol_version=self._protocol_version,
            supported_methods=tuple(self._supported_methods),
            directory_resolver=self._directory_resolver,
            session_claim=self._session_claim,
            session_claim_finalize=self._session_claim_finalize,
            session_claim_release=self._session_claim_release,
            error_response=self._generate_error_response,
            success_response=self._jsonrpc_success_response,
        )
        self._extension_method_registry = build_extension_method_registry(
            self._extension_handler_context
        )

    async def _handle_requests(self, request: Request) -> Response:
        # Fast path: sniff method first then either handle here or delegate.
        request_id: str | int | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get("id")
                if request_id is not None and not isinstance(request_id, str | int):
                    request_id = None

            if not self._allowed_content_length(request):
                return self._generate_error_response(
                    request_id,
                    A2AError(root=InvalidRequestError(message="Payload too large")),
                )

            base_request = JSONRPCRequest.model_validate(body)
        except Exception:
            # Delegate to base implementation for consistent error handling.
            return await super()._handle_requests(request)

        extension_spec = self._extension_method_registry.resolve(base_request.method)
        if extension_spec is None:
            if base_request.method in CORE_JSONRPC_METHODS:
                return await super()._handle_requests(request)
            if base_request.id is None:
                return Response(status_code=204)

            return self._generate_error_response(
                base_request.id,
                method_not_supported_error(
                    method=base_request.method,
                    supported_methods=self._supported_methods,
                    protocol_version=self._protocol_version,
                ),
            )

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                invalid_params_error("params must be an object"),
            )
        return await extension_spec.handler(
            self._extension_handler_context,
            base_request,
            params,
            request,
        )

    def _jsonrpc_success_response(self, request_id: str | int, result: Any) -> JSONResponse:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )

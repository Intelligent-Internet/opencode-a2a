from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import (
    A2AError,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONRPCError,
    JSONRPCRequest,
)
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from ..contracts.extensions import (
    INTERRUPT_ERROR_BUSINESS_CODES,
    PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES,
    SESSION_QUERY_ERROR_BUSINESS_CODES,
)
from ..opencode_upstream_client import OpencodeUpstreamClient, UpstreamContractError
from .methods import (
    SESSION_CONTEXT_PREFIX,
    _apply_session_query_limit,
    _as_a2a_message,
    _as_a2a_session_task,
    _extract_provider_catalog,
    _extract_raw_items,
    _normalize_model_summaries,
    _normalize_permission_reply,
    _normalize_provider_summaries,
    _parse_question_answers,
    _PromptAsyncValidationError,
    _validate_command_request_payload,
    _validate_prompt_async_format,
    _validate_prompt_async_part,
    _validate_prompt_async_request_payload,
    _validate_shell_request_payload,
)
from .params import (
    JsonRpcParamsValidationError,
    parse_get_session_messages_params,
    parse_list_sessions_params,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SESSION_CONTEXT_PREFIX",
    "_PromptAsyncValidationError",
    "_extract_provider_catalog",
    "_normalize_model_summaries",
    "_normalize_permission_reply",
    "_normalize_provider_summaries",
    "_parse_question_answers",
    "_validate_command_request_payload",
    "_validate_prompt_async_format",
    "_validate_prompt_async_part",
    "_validate_shell_request_payload",
]

ERR_SESSION_NOT_FOUND = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_NOT_FOUND"]
ERR_SESSION_FORBIDDEN = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_FORBIDDEN"]
ERR_METHOD_NOT_SUPPORTED = -32601
ERR_UPSTREAM_UNREACHABLE = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_INTERRUPT_NOT_FOUND = INTERRUPT_ERROR_BUSINESS_CODES["INTERRUPT_REQUEST_NOT_FOUND"]
ERR_UPSTREAM_PAYLOAD_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_PAYLOAD_ERROR"]
ERR_DISCOVERY_UPSTREAM_UNREACHABLE = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_DISCOVERY_UPSTREAM_HTTP_ERROR = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_DISCOVERY_UPSTREAM_PAYLOAD_ERROR = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES[
    "UPSTREAM_PAYLOAD_ERROR"
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

    def _session_forbidden_response(
        self,
        request_id: str | int | None,
        *,
        session_id: str,
    ) -> Response:
        return self._generate_error_response(
            request_id,
            JSONRPCError(
                code=ERR_SESSION_FORBIDDEN,
                message="Session forbidden",
                data={"type": "SESSION_FORBIDDEN", "session_id": session_id},
            ),
        )

    def _extract_directory_from_metadata(
        self,
        *,
        request_id: str | int | None,
        params: dict[str, Any],
    ) -> tuple[str | None, Response | None]:
        metadata = params.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return None, self._generate_error_response(
                request_id,
                A2AError(
                    root=InvalidParamsError(
                        message="metadata must be an object",
                        data={"type": "INVALID_FIELD", "field": "metadata"},
                    )
                ),
            )

        opencode_metadata: dict[str, Any] | None = None
        if isinstance(metadata, dict):
            unknown_metadata_fields = sorted(set(metadata) - {"opencode", "shared"})
            if unknown_metadata_fields:
                prefixed_fields = [f"metadata.{field}" for field in unknown_metadata_fields]
                return None, self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidParamsError(
                            message=f"Unsupported metadata fields: {', '.join(prefixed_fields)}",
                            data={"type": "INVALID_FIELD", "fields": prefixed_fields},
                        )
                    ),
                )
            raw_opencode_metadata = metadata.get("opencode")
            if raw_opencode_metadata is not None and not isinstance(raw_opencode_metadata, dict):
                return None, self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidParamsError(
                            message="metadata.opencode must be an object",
                            data={"type": "INVALID_FIELD", "field": "metadata.opencode"},
                        )
                    ),
                )
            if isinstance(raw_opencode_metadata, dict):
                opencode_metadata = raw_opencode_metadata
            raw_shared_metadata = metadata.get("shared")
            if raw_shared_metadata is not None and not isinstance(raw_shared_metadata, dict):
                return None, self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidParamsError(
                            message="metadata.shared must be an object",
                            data={"type": "INVALID_FIELD", "field": "metadata.shared"},
                        )
                    ),
                )

        directory = None
        if opencode_metadata is not None:
            directory = opencode_metadata.get("directory")
        if directory is not None and not isinstance(directory, str):
            return None, self._generate_error_response(
                request_id,
                A2AError(
                    root=InvalidParamsError(
                        message="metadata.opencode.directory must be a string",
                        data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
                    )
                ),
            )

        return directory, None

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

        session_query_methods = {
            self._method_list_sessions,
            self._method_get_session_messages,
        }
        provider_discovery_methods = {
            self._method_list_providers,
            self._method_list_models,
        }
        session_control_methods = {
            self._method_prompt_async,
            self._method_command,
        }
        if self._method_shell is not None:
            session_control_methods.add(self._method_shell)
        interrupt_callback_methods = {
            self._method_reply_permission,
            self._method_reply_question,
            self._method_reject_question,
        }
        if (
            base_request.method
            not in session_query_methods
            | provider_discovery_methods
            | session_control_methods
            | interrupt_callback_methods
        ):
            core_methods = {
                "message/send",
                "message/stream",
                "tasks/get",
                "tasks/cancel",
                "tasks/resubscribe",
            }
            if base_request.method in core_methods:
                return await super()._handle_requests(request)

            if base_request.id is None:
                return Response(status_code=204)

            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_METHOD_NOT_SUPPORTED,
                    message=f"Unsupported method: {base_request.method}",
                    data={
                        "type": "METHOD_NOT_SUPPORTED",
                        "method": base_request.method,
                        "supported_methods": self._supported_methods,
                        "protocol_version": self._protocol_version,
                    },
                ),
            )

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InvalidParamsError(message="params must be an object")),
            )

        if base_request.method in session_query_methods:
            return await self._handle_session_query_request(base_request, params)
        if base_request.method in provider_discovery_methods:
            return await self._handle_provider_discovery_request(base_request, params)
        if base_request.method in session_control_methods:
            return await self._handle_session_control_request(
                base_request,
                params,
                request=request,
            )
        return await self._handle_interrupt_callback_request(base_request, params, request=request)

    async def _handle_session_query_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
    ) -> Response:
        try:
            if base_request.method == self._method_list_sessions:
                query = parse_list_sessions_params(params)
                session_id: str | None = None
            else:
                session_id, query = parse_get_session_messages_params(params)
        except JsonRpcParamsValidationError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data=exc.data,
                    )
                ),
            )

        limit = int(query["limit"])
        try:
            if base_request.method == self._method_list_sessions:
                raw_result = await self._upstream_client.list_sessions(params=query)
            else:
                assert session_id is not None
                raw_result = await self._upstream_client.list_messages(session_id, params=query)
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404 and base_request.method == self._method_get_session_messages:
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_SESSION_NOT_FOUND,
                        message="Session not found",
                        data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
                    ),
                )
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_HTTP_ERROR,
                    message="Upstream OpenCode error",
                    data={
                        "type": "UPSTREAM_HTTP_ERROR",
                        "upstream_status": upstream_status,
                    },
                ),
            )
        except httpx.HTTPError:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE"},
                ),
            )
        except Exception as exc:
            logger.exception("OpenCode session query JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        try:
            if base_request.method == self._method_list_sessions:
                raw_items = _extract_raw_items(raw_result, kind="sessions")
            else:
                raw_items = _extract_raw_items(raw_result, kind="messages")
        except ValueError as exc:
            logger.warning("Upstream OpenCode payload mismatch: %s", exc)
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_PAYLOAD_ERROR,
                    message="Upstream OpenCode payload mismatch",
                    data={"type": "UPSTREAM_PAYLOAD_ERROR", "detail": str(exc)},
                ),
            )

        # Protocol: items are always arrays of A2A objects.
        # Task for sessions; Message for messages.
        if base_request.method == self._method_list_sessions:
            mapped: list[dict[str, Any]] = []
            for item in raw_items:
                task = _as_a2a_session_task(item)
                if task is not None:
                    mapped.append(task)
            # OpenCode documents `limit` for message history, not for session list.
            # Enforce the adapter contract locally so the declared pagination stays true.
            items: list[dict[str, Any]] = _apply_session_query_limit(mapped, limit=limit)
        else:
            assert session_id is not None
            mapped = []
            for item in raw_items:
                message = _as_a2a_message(session_id, item)
                if message is not None:
                    mapped.append(message)
            items = mapped

        result = {
            "items": items,
        }

        # Notifications (id omitted) should not yield a response.
        if base_request.id is None:
            return Response(status_code=204)

        return self._jsonrpc_success_response(
            base_request.id,
            result,
        )

    async def _handle_provider_discovery_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
    ) -> Response:
        allowed_fields = {"metadata"}
        if base_request.method == self._method_list_models:
            allowed_fields.add("provider_id")
        unknown_fields = sorted(set(params) - allowed_fields)
        if unknown_fields:
            prefixed_fields = [f"params.{field}" for field in unknown_fields]
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=f"Unsupported params fields: {', '.join(prefixed_fields)}",
                        data={"type": "INVALID_FIELD", "fields": prefixed_fields},
                    )
                ),
            )

        provider_id: str | None = None
        if base_request.method == self._method_list_models:
            raw_provider_id = params.get("provider_id")
            if raw_provider_id is not None:
                if not isinstance(raw_provider_id, str) or not raw_provider_id.strip():
                    return self._generate_error_response(
                        base_request.id,
                        A2AError(
                            root=InvalidParamsError(
                                message="provider_id must be a non-empty string",
                                data={"type": "INVALID_FIELD", "field": "provider_id"},
                            )
                        ),
                    )
                provider_id = raw_provider_id.strip()

        directory, metadata_error = self._extract_directory_from_metadata(
            request_id=base_request.id,
            params=params,
        )
        if metadata_error is not None:
            return metadata_error

        try:
            directory = self._directory_resolver(directory)
        except ValueError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
                    )
                ),
            )

        try:
            raw_result = await self._upstream_client.list_provider_catalog(directory=directory)
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_DISCOVERY_UPSTREAM_HTTP_ERROR,
                    message="Upstream OpenCode error",
                    data={
                        "type": "UPSTREAM_HTTP_ERROR",
                        "method": base_request.method,
                        "upstream_status": upstream_status,
                    },
                ),
            )
        except httpx.HTTPError:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_DISCOVERY_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE", "method": base_request.method},
                ),
            )
        except Exception as exc:
            logger.exception("OpenCode provider discovery JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        try:
            raw_providers, default_by_provider, connected = _extract_provider_catalog(raw_result)
            if base_request.method == self._method_list_providers:
                items = _normalize_provider_summaries(
                    raw_providers,
                    default_by_provider=default_by_provider,
                    connected=connected,
                )
            else:
                items = _normalize_model_summaries(
                    raw_providers,
                    default_by_provider=default_by_provider,
                    connected=connected,
                    provider_id=provider_id,
                )
        except ValueError as exc:
            logger.warning("Upstream OpenCode provider payload mismatch: %s", exc)
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_DISCOVERY_UPSTREAM_PAYLOAD_ERROR,
                    message="Upstream OpenCode payload mismatch",
                    data={
                        "type": "UPSTREAM_PAYLOAD_ERROR",
                        "method": base_request.method,
                        "detail": str(exc),
                    },
                ),
            )

        result = {
            "items": items,
            "default_by_provider": default_by_provider,
            "connected": connected,
        }

        if base_request.id is None:
            return Response(status_code=204)

        return self._jsonrpc_success_response(base_request.id, result)

    async def _handle_session_control_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
        *,
        request: Request,
    ) -> Response:
        allowed_fields = {"session_id", "request", "metadata"}
        unknown_fields = sorted(set(params) - allowed_fields)
        if unknown_fields:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=f"Unsupported fields: {', '.join(unknown_fields)}",
                        data={"type": "INVALID_FIELD", "fields": unknown_fields},
                    )
                ),
            )

        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Missing required params.session_id",
                        data={"type": "MISSING_FIELD", "field": "session_id"},
                    )
                ),
            )
        session_id = session_id.strip()

        raw_request = params.get("request")
        if raw_request is None:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Missing required params.request",
                        data={"type": "MISSING_FIELD", "field": "request"},
                    )
                ),
            )
        if not isinstance(raw_request, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="params.request must be an object",
                        data={"type": "INVALID_FIELD", "field": "request"},
                    )
                ),
            )

        request_identity = getattr(request.state, "user_identity", None)
        identity = request_identity if isinstance(request_identity, str) else None
        task_id = getattr(request.state, "task_id", None)
        context_id = getattr(request.state, "context_id", None)

        def _log_shell_audit(outcome: str) -> None:
            if base_request.method != self._method_shell:
                return
            logger.info(
                "session_shell_audit method=%s identity=%s task_id=%s context_id=%s "
                "session_id=%s outcome=%s",
                base_request.method,
                identity if identity else "-",
                task_id if isinstance(task_id, str) and task_id.strip() else "-",
                context_id if isinstance(context_id, str) and context_id.strip() else "-",
                session_id,
                outcome,
            )

        try:
            if base_request.method == self._method_prompt_async:
                _validate_prompt_async_request_payload(raw_request)
            elif base_request.method == self._method_command:
                _validate_command_request_payload(raw_request)
            elif base_request.method == self._method_shell:
                _validate_shell_request_payload(raw_request)
            else:
                raise _PromptAsyncValidationError(
                    field="method",
                    message=f"Unsupported method: {base_request.method}",
                )
        except _PromptAsyncValidationError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD", "field": exc.field},
                    )
                ),
            )

        directory, metadata_error = self._extract_directory_from_metadata(
            request_id=base_request.id,
            params=params,
        )
        if metadata_error is not None:
            return metadata_error

        try:
            directory = self._directory_resolver(directory)
        except ValueError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
                    )
                ),
            )

        pending_claim = False
        claim_finalized = False
        if identity:
            try:
                pending_claim = await self._session_claim(
                    identity=identity,
                    session_id=session_id,
                )
            except PermissionError:
                _log_shell_audit("forbidden")
                return self._session_forbidden_response(
                    base_request.id,
                    session_id=session_id,
                )

        try:
            result: dict[str, Any]
            if base_request.method == self._method_prompt_async:
                await self._upstream_client.session_prompt_async(
                    session_id,
                    request=dict(raw_request),
                    directory=directory,
                )
                result = {"ok": True, "session_id": session_id}
            elif base_request.method == self._method_command:
                raw_result = await self._upstream_client.session_command(
                    session_id,
                    request=dict(raw_request),
                    directory=directory,
                )
                item = _as_a2a_message(session_id, raw_result)
                if item is None:
                    raise UpstreamContractError(
                        "OpenCode /session/{sessionID}/command response could not be mapped "
                        "to A2A Message"
                    )
                result = {"item": item}
            else:
                raw_result = await self._upstream_client.session_shell(
                    session_id,
                    request=dict(raw_request),
                    directory=directory,
                )
                item = _as_a2a_message(session_id, raw_result)
                if item is None:
                    raise UpstreamContractError(
                        "OpenCode /session/{sessionID}/shell response could not be mapped "
                        "to A2A Message"
                    )
                result = {"item": item}

            if pending_claim and identity:
                await self._session_claim_finalize(
                    identity=identity,
                    session_id=session_id,
                )
                claim_finalized = True
            _log_shell_audit("success")
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404:
                _log_shell_audit("upstream_404")
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_SESSION_NOT_FOUND,
                        message="Session not found",
                        data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
                    ),
                )
            _log_shell_audit("upstream_http_error")
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_HTTP_ERROR,
                    message="Upstream OpenCode error",
                    data={
                        "type": "UPSTREAM_HTTP_ERROR",
                        "method": base_request.method,
                        "upstream_status": upstream_status,
                        "session_id": session_id,
                    },
                ),
            )
        except httpx.HTTPError:
            _log_shell_audit("upstream_unreachable")
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={
                        "type": "UPSTREAM_UNREACHABLE",
                        "method": base_request.method,
                        "session_id": session_id,
                    },
                ),
            )
        except UpstreamContractError as exc:
            _log_shell_audit("upstream_payload_error")
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_PAYLOAD_ERROR,
                    message="Upstream OpenCode payload mismatch",
                    data={
                        "type": "UPSTREAM_PAYLOAD_ERROR",
                        "method": base_request.method,
                        "detail": str(exc),
                        "session_id": session_id,
                    },
                ),
            )
        except PermissionError:
            _log_shell_audit("forbidden")
            return self._session_forbidden_response(
                base_request.id,
                session_id=session_id,
            )
        except Exception as exc:
            _log_shell_audit("internal_error")
            logger.exception("OpenCode session control JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )
        finally:
            if pending_claim and not claim_finalized and identity:
                try:
                    await self._session_claim_release(
                        identity=identity,
                        session_id=session_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to release pending session claim for session_id=%s",
                        session_id,
                    )

        if base_request.id is None:
            return Response(status_code=204)
        return self._jsonrpc_success_response(
            base_request.id,
            result,
        )

    async def _handle_interrupt_callback_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
        *,
        request: Request,
    ) -> Response:
        request_id = params.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Missing required params.request_id",
                        data={"type": "MISSING_FIELD", "field": "request_id"},
                    )
                ),
            )
        request_id = request_id.strip()
        request_identity = getattr(request.state, "user_identity", None)
        directory, metadata_error = self._extract_directory_from_metadata(
            request_id=base_request.id,
            params=params,
        )
        if metadata_error is not None:
            return metadata_error
        expected_interrupt_type = (
            "permission" if base_request.method == self._method_reply_permission else "question"
        )
        resolve_request = getattr(self._upstream_client, "resolve_interrupt_request", None)
        if callable(resolve_request):
            status, binding = resolve_request(request_id)
            if status != "active" or binding is None:
                error_type = (
                    "INTERRUPT_REQUEST_EXPIRED"
                    if status == "expired"
                    else "INTERRUPT_REQUEST_NOT_FOUND"
                )
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_INTERRUPT_NOT_FOUND,
                        message=(
                            "Interrupt request expired"
                            if status == "expired"
                            else "Interrupt request not found"
                        ),
                        data={"type": error_type, "request_id": request_id},
                    ),
                )
            if binding.interrupt_type != expected_interrupt_type:
                return self._generate_error_response(
                    base_request.id,
                    A2AError(
                        root=InvalidParamsError(
                            message=(
                                "Interrupt type mismatch: "
                                f"expected {expected_interrupt_type}, got {binding.interrupt_type}"
                            ),
                            data={
                                "type": "INTERRUPT_TYPE_MISMATCH",
                                "request_id": request_id,
                                "expected": expected_interrupt_type,
                                "actual": binding.interrupt_type,
                            },
                        )
                    ),
                )
            if (
                isinstance(request_identity, str)
                and request_identity
                and binding.identity
                and binding.identity != request_identity
            ):
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_INTERRUPT_NOT_FOUND,
                        message="Interrupt request not found",
                        data={
                            "type": "INTERRUPT_REQUEST_NOT_FOUND",
                            "request_id": request_id,
                        },
                    ),
                )
        else:
            resolve_session = getattr(self._upstream_client, "resolve_interrupt_session", None)
            if callable(resolve_session):
                if not resolve_session(request_id):
                    return self._generate_error_response(
                        base_request.id,
                        JSONRPCError(
                            code=ERR_INTERRUPT_NOT_FOUND,
                            message="Interrupt request not found",
                            data={
                                "type": "INTERRUPT_REQUEST_NOT_FOUND",
                                "request_id": request_id,
                            },
                        ),
                    )
        if base_request.method == self._method_reply_permission:
            allowed_fields = {"request_id", "reply", "message", "metadata"}
        elif base_request.method == self._method_reply_question:
            allowed_fields = {"request_id", "answers", "metadata"}
        else:
            allowed_fields = {"request_id", "metadata"}
        unknown_fields = sorted(set(params) - allowed_fields)
        if unknown_fields:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=f"Unsupported fields: {', '.join(unknown_fields)}",
                        data={"type": "INVALID_FIELD", "fields": unknown_fields},
                    )
                ),
            )

        try:
            result: dict[str, Any] = {
                "ok": True,
                "request_id": request_id,
            }
            if base_request.method == self._method_reply_permission:
                reply = _normalize_permission_reply(params.get("reply"))
                message = params.get("message")
                if message is not None and not isinstance(message, str):
                    raise ValueError("message must be a string")
                await self._upstream_client.permission_reply(
                    request_id,
                    reply=reply,
                    message=message,
                    directory=directory,
                )
            elif base_request.method == self._method_reply_question:
                answers = _parse_question_answers(params.get("answers"))
                await self._upstream_client.question_reply(
                    request_id,
                    answers=answers,
                    directory=directory,
                )
            else:
                await self._upstream_client.question_reject(request_id, directory=directory)
            discard_request = getattr(self._upstream_client, "discard_interrupt_request", None)
            if callable(discard_request):
                discard_request(request_id)
        except ValueError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD"},
                    )
                ),
            )
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404:
                discard_request = getattr(self._upstream_client, "discard_interrupt_request", None)
                if callable(discard_request):
                    discard_request(request_id)
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_INTERRUPT_NOT_FOUND,
                        message="Interrupt request not found",
                        data={
                            "type": "INTERRUPT_REQUEST_NOT_FOUND",
                            "request_id": request_id,
                        },
                    ),
                )
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_HTTP_ERROR,
                    message="Upstream OpenCode error",
                    data={
                        "type": "UPSTREAM_HTTP_ERROR",
                        "upstream_status": upstream_status,
                        "request_id": request_id,
                    },
                ),
            )
        except httpx.HTTPError:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE", "request_id": request_id},
                ),
            )
        except Exception as exc:
            logger.exception("OpenCode interrupt callback JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        if base_request.id is None:
            return Response(status_code=204)
        return self._jsonrpc_success_response(base_request.id, result)

    def _jsonrpc_success_response(self, request_id: str | int, result: Any) -> JSONResponse:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )

from __future__ import annotations

import logging
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import Response

from ...auth import (
    CAPABILITY_SESSION_SHELL,
    request_has_capability,
)
from ...contracts.extensions import SESSION_QUERY_ERROR_BUSINESS_CODES
from ...invocation import call_with_supported_kwargs
from ...opencode_upstream_client import UpstreamConcurrencyLimitError, UpstreamContractError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error, session_not_found_error
from ..methods import (
    _as_a2a_message,
    _PromptAsyncValidationError,
    _validate_command_request_payload,
    _validate_prompt_async_request_payload,
    _validate_shell_request_payload,
)
from ..models import JSONRPCRequest
from .common import (
    build_authorization_forbidden_response,
    build_session_forbidden_response,
    build_success_response,
    build_upstream_exception_response,
    claim_session,
    reject_unknown_fields,
    resolve_routing_context,
)

logger = logging.getLogger(__name__)

ERR_SESSION_NOT_FOUND = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_NOT_FOUND"]
ERR_UPSTREAM_UNREACHABLE = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_UPSTREAM_PAYLOAD_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_PAYLOAD_ERROR"]


def _shell_audit_outcome(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 404:
            return "upstream_404"
        return "upstream_http_error"
    if isinstance(exc, httpx.HTTPError):
        return "upstream_unreachable"
    if isinstance(exc, UpstreamConcurrencyLimitError):
        return "upstream_backpressure"
    if isinstance(exc, (UpstreamContractError, ValueError)):
        return "upstream_payload_error"
    if isinstance(exc, PermissionError):
        return "forbidden"
    return "internal_error"


async def handle_session_control_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    unknown_fields_error = reject_unknown_fields(
        context,
        base_request.id,
        params,
        allowed_fields={"session_id", "request", "metadata"},
    )
    if unknown_fields_error is not None:
        return unknown_fields_error

    session_id = params.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return context.error_response(
            base_request.id,
            invalid_params_error(
                "Missing required params.session_id",
                data={"type": "MISSING_FIELD", "field": "session_id"},
            ),
        )
    session_id = session_id.strip()

    raw_request = params.get("request")
    if raw_request is None:
        return context.error_response(
            base_request.id,
            invalid_params_error(
                "Missing required params.request",
                data={"type": "MISSING_FIELD", "field": "request"},
            ),
        )
    if not isinstance(raw_request, dict):
        return context.error_response(
            base_request.id,
            invalid_params_error(
                "params.request must be an object",
                data={"type": "INVALID_FIELD", "field": "request"},
            ),
        )

    request_identity = getattr(request.state, "user_identity", None)
    identity = request_identity if isinstance(request_identity, str) else None
    task_id = getattr(request.state, "task_id", None)
    context_id = getattr(request.state, "context_id", None)

    def _log_shell_audit(outcome: str) -> None:
        if base_request.method != context.method_shell:
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

    if base_request.method == context.method_shell and not request_has_capability(
        request,
        CAPABILITY_SESSION_SHELL,
    ):
        _log_shell_audit("authorization_forbidden")
        credential_id = getattr(request.state, "user_credential_id", None)
        return build_authorization_forbidden_response(
            context,
            base_request.id,
            method=base_request.method,
            capability=CAPABILITY_SESSION_SHELL,
            credential_id=credential_id if isinstance(credential_id, str) else None,
            error_code=SESSION_QUERY_ERROR_BUSINESS_CODES["AUTHORIZATION_FORBIDDEN"],
        )

    try:
        if base_request.method == context.method_prompt_async:
            _validate_prompt_async_request_payload(raw_request)
        elif base_request.method == context.method_command:
            _validate_command_request_payload(raw_request)
        elif base_request.method == context.method_shell:
            _validate_shell_request_payload(raw_request)
        else:
            raise _PromptAsyncValidationError(
                field="method",
                message=f"Unsupported method: {base_request.method}",
            )
    except _PromptAsyncValidationError as exc:
        return context.error_response(
            base_request.id,
            invalid_params_error(str(exc), data={"type": "INVALID_FIELD", "field": exc.field}),
        )

    directory, workspace_id, routing_error = resolve_routing_context(
        context,
        request_id=base_request.id,
        params=params,
    )
    if routing_error is not None:
        return routing_error

    try:
        async with claim_session(
            context,
            identity=identity,
            session_id=session_id,
            logger=logger,
        ) as session_claim:
            result: dict[str, Any]
            if base_request.method == context.method_prompt_async:
                await call_with_supported_kwargs(
                    context.upstream_client.session_prompt_async,
                    session_id,
                    request=dict(raw_request),
                    directory=directory,
                    workspace_id=workspace_id,
                )
                result = {"ok": True, "session_id": session_id}
            elif base_request.method == context.method_command:
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.session_command,
                    session_id,
                    request=dict(raw_request),
                    directory=directory,
                    workspace_id=workspace_id,
                )
                item = _as_a2a_message(session_id, raw_result)
                if item is None:
                    raise UpstreamContractError(
                        "OpenCode /session/{sessionID}/command response could not be mapped "
                        "to A2A Message"
                    )
                result = {"item": item}
            else:
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.session_shell,
                    session_id,
                    request=dict(raw_request),
                    directory=directory,
                    workspace_id=workspace_id,
                )
                item = _as_a2a_message(session_id, raw_result)
                if item is None:
                    raise UpstreamContractError(
                        "OpenCode /session/{sessionID}/shell response could not be mapped "
                        "to A2A Message"
                    )
                result = {"item": item}

            await session_claim.finalize()
            _log_shell_audit("success")
    except Exception as exc:
        _log_shell_audit(_shell_audit_outcome(exc))
        return build_upstream_exception_response(
            context,
            base_request.id,
            exc=exc,
            upstream_http_error_code=ERR_UPSTREAM_HTTP_ERROR,
            upstream_unreachable_error_code=ERR_UPSTREAM_UNREACHABLE,
            upstream_payload_error_code=ERR_UPSTREAM_PAYLOAD_ERROR,
            internal_log_message="OpenCode session control JSON-RPC method failed",
            method=base_request.method,
            session_id=session_id,
            on_not_found=lambda: context.error_response(
                base_request.id,
                session_not_found_error(ERR_SESSION_NOT_FOUND, session_id=session_id),
            ),
            on_permission_error=lambda: build_session_forbidden_response(
                context,
                base_request.id,
                session_id=session_id,
            ),
        )

    return build_success_response(context, base_request.id, result)

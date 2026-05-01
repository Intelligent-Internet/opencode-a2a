from __future__ import annotations

import logging
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import Response

from ...contracts.extensions import INTERRUPT_ERROR_BUSINESS_CODES
from ...opencode_upstream_client import UpstreamConcurrencyLimitError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import (
    interrupt_not_found_error,
    interrupt_type_mismatch_error,
    invalid_params_error,
)
from ..methods import _normalize_permission_reply, _parse_question_answers
from ..models import JSONRPCRequest
from .common import (
    build_internal_error_response,
    build_success_response,
    build_upstream_concurrency_error_response,
    build_upstream_http_error_response,
    build_upstream_unreachable_error_response,
    extract_interrupt_callback_directory_hint,
    extract_workspace_id_from_metadata,
    reject_unknown_fields,
)

logger = logging.getLogger(__name__)

ERR_INTERRUPT_NOT_FOUND = INTERRUPT_ERROR_BUSINESS_CODES["INTERRUPT_REQUEST_NOT_FOUND"]
ERR_INTERRUPT_EXPIRED = INTERRUPT_ERROR_BUSINESS_CODES["INTERRUPT_REQUEST_EXPIRED"]
ERR_INTERRUPT_TYPE_MISMATCH = INTERRUPT_ERROR_BUSINESS_CODES["INTERRUPT_TYPE_MISMATCH"]
ERR_UPSTREAM_UNREACHABLE = INTERRUPT_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = INTERRUPT_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]


async def handle_interrupt_callback_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    request_id = params.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return context.error_response(
            base_request.id,
            invalid_params_error(
                "Missing required params.request_id",
                data={"type": "MISSING_FIELD", "field": "request_id"},
            ),
        )
    request_id = request_id.strip()
    request_identity = getattr(request.state, "user_identity", None)
    request_credential_id = getattr(request.state, "user_credential_id", None)

    directory, directory_error = extract_interrupt_callback_directory_hint(
        context,
        request_id=base_request.id,
        params=params,
    )
    if directory_error is not None:
        return directory_error
    workspace_id, workspace_error = extract_workspace_id_from_metadata(
        context,
        request_id=base_request.id,
        params=params,
    )
    if workspace_error is not None:
        return workspace_error
    routing_kwargs: dict[str, Any] = {}
    if directory is not None:
        routing_kwargs["directory"] = directory
    if workspace_id is not None:
        routing_kwargs["workspace_id"] = workspace_id

    expected_interrupt_type = (
        "permission" if base_request.method == context.method_reply_permission else "question"
    )
    resolve_request = getattr(context.upstream_client, "resolve_interrupt_request", None)
    if callable(resolve_request):
        status, binding = await resolve_request(request_id)
        if status != "active" or binding is None:
            return context.error_response(
                base_request.id,
                interrupt_not_found_error(
                    ERR_INTERRUPT_EXPIRED if status == "expired" else ERR_INTERRUPT_NOT_FOUND,
                    request_id=request_id,
                    expired=status == "expired",
                ),
            )
        if binding.interrupt_type != expected_interrupt_type:
            return context.error_response(
                base_request.id,
                interrupt_type_mismatch_error(
                    ERR_INTERRUPT_TYPE_MISMATCH,
                    request_id=request_id,
                    expected_interrupt_type=expected_interrupt_type,
                    actual_interrupt_type=binding.interrupt_type,
                ),
            )
        if (
            isinstance(request_identity, str)
            and request_identity
            and binding.identity
            and binding.identity != request_identity
        ):
            return context.error_response(
                base_request.id,
                interrupt_not_found_error(
                    ERR_INTERRUPT_NOT_FOUND,
                    request_id=request_id,
                ),
            )
        if (
            isinstance(request_credential_id, str)
            and request_credential_id
            and binding.credential_id
            and binding.credential_id != request_credential_id
        ):
            return context.error_response(
                base_request.id,
                interrupt_not_found_error(
                    ERR_INTERRUPT_NOT_FOUND,
                    request_id=request_id,
                ),
            )
    else:
        resolve_session = getattr(context.upstream_client, "resolve_interrupt_session", None)
        if callable(resolve_session) and not await resolve_session(request_id):
            return context.error_response(
                base_request.id,
                interrupt_not_found_error(
                    ERR_INTERRUPT_NOT_FOUND,
                    request_id=request_id,
                ),
            )

    if base_request.method == context.method_reply_permission:
        allowed_fields = {"request_id", "reply", "message", "metadata"}
    elif base_request.method == context.method_reply_question:
        allowed_fields = {"request_id", "answers", "metadata"}
    else:
        allowed_fields = {"request_id", "metadata"}
    unknown_fields_error = reject_unknown_fields(
        context,
        base_request.id,
        params,
        allowed_fields=allowed_fields,
    )
    if unknown_fields_error is not None:
        return unknown_fields_error

    try:
        result: dict[str, Any] = {
            "ok": True,
            "request_id": request_id,
        }
        if base_request.method == context.method_reply_permission:
            reply = _normalize_permission_reply(params.get("reply"))
            message = params.get("message")
            if message is not None and not isinstance(message, str):
                raise ValueError("message must be a string")
            await context.upstream_client.permission_reply(
                request_id,
                reply=reply,
                message=message,
                **routing_kwargs,
            )
        elif base_request.method == context.method_reply_question:
            answers = _parse_question_answers(params.get("answers"))
            await context.upstream_client.question_reply(
                request_id,
                answers=answers,
                **routing_kwargs,
            )
        else:
            await context.upstream_client.question_reject(
                request_id,
                **routing_kwargs,
            )
        discard_request = getattr(context.upstream_client, "discard_interrupt_request", None)
        if callable(discard_request):
            await discard_request(request_id)
    except ValueError as exc:
        return context.error_response(
            base_request.id,
            invalid_params_error(str(exc), data={"type": "INVALID_FIELD"}),
        )
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status == 404:
            discard_request = getattr(context.upstream_client, "discard_interrupt_request", None)
            if callable(discard_request):
                await discard_request(request_id)
            return context.error_response(
                base_request.id,
                interrupt_not_found_error(
                    ERR_INTERRUPT_NOT_FOUND,
                    request_id=request_id,
                ),
            )
        return build_upstream_http_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_HTTP_ERROR,
            upstream_status=upstream_status,
            interrupt_request_id=request_id,
        )
    except httpx.HTTPError:
        return build_upstream_unreachable_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_UNREACHABLE,
            interrupt_request_id=request_id,
        )
    except UpstreamConcurrencyLimitError as exc:
        return build_upstream_concurrency_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_UNREACHABLE,
            exc=exc,
            interrupt_request_id=request_id,
        )
    except Exception as exc:
        return build_internal_error_response(
            context,
            base_request.id,
            log_message="OpenCode interrupt callback JSON-RPC method failed",
            exc=exc,
        )

    return build_success_response(context, base_request.id, result)

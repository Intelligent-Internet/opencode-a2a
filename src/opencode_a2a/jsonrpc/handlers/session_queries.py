from __future__ import annotations

import logging
from typing import Any

import httpx
from a2a.types import JSONRPCRequest
from starlette.requests import Request
from starlette.responses import Response

from ...contracts.extensions import SESSION_QUERY_ERROR_BUSINESS_CODES
from ...invocation import call_with_supported_kwargs
from ...opencode_upstream_client import UpstreamConcurrencyLimitError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error, session_not_found_error
from ..methods import (
    _apply_session_query_limit,
    _as_a2a_message,
    _as_a2a_session_task,
    _extract_raw_items,
)
from ..params import (
    JsonRpcParamsValidationError,
    parse_get_session_messages_params,
    parse_list_sessions_params,
)
from .common import (
    build_internal_error_response,
    build_success_response,
    build_upstream_concurrency_error_response,
    build_upstream_http_error_response,
    build_upstream_payload_error_response,
    build_upstream_unreachable_error_response,
    resolve_routing_context,
)

logger = logging.getLogger(__name__)

ERR_SESSION_NOT_FOUND = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_NOT_FOUND"]
ERR_UPSTREAM_UNREACHABLE = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_UPSTREAM_PAYLOAD_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_PAYLOAD_ERROR"]


async def handle_session_query_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    try:
        if base_request.method == context.method_list_sessions:
            query = parse_list_sessions_params(params)
            session_id: str | None = None
        else:
            session_id, query = parse_get_session_messages_params(params)
    except JsonRpcParamsValidationError as exc:
        return context.error_response(
            base_request.id,
            invalid_params_error(str(exc), data=exc.data),
        )

    limit = int(query["limit"])
    directory = None
    workspace_id = None
    if base_request.method == context.method_list_sessions:
        requested_directory = query.pop("directory", None)
        if requested_directory is not None and not isinstance(requested_directory, str):
            return context.error_response(
                base_request.id,
                invalid_params_error(
                    "directory must be a string",
                    data={"type": "INVALID_FIELD", "field": "directory"},
                ),
            )
        directory, workspace_id, routing_error = resolve_routing_context(
            context,
            request_id=base_request.id,
            params=params,
            requested_directory=requested_directory,
        )
        if routing_error is not None:
            return routing_error
    else:
        directory, workspace_id, routing_error = resolve_routing_context(
            context,
            request_id=base_request.id,
            params=params,
        )
        if routing_error is not None:
            return routing_error
    try:
        if base_request.method == context.method_list_sessions:
            raw_result = await call_with_supported_kwargs(
                context.upstream_client.list_sessions,
                params=query,
                directory=directory,
                workspace_id=workspace_id,
            )
        else:
            assert session_id is not None
            raw_result = await call_with_supported_kwargs(
                context.upstream_client.list_messages,
                session_id,
                params=query,
                workspace_id=workspace_id,
            )
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status == 404 and base_request.method == context.method_get_session_messages:
            assert session_id is not None
            return context.error_response(
                base_request.id,
                session_not_found_error(ERR_SESSION_NOT_FOUND, session_id=session_id),
            )
        return build_upstream_http_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_HTTP_ERROR,
            upstream_status=upstream_status,
        )
    except httpx.HTTPError:
        return build_upstream_unreachable_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_UNREACHABLE,
        )
    except UpstreamConcurrencyLimitError as exc:
        return build_upstream_concurrency_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_UNREACHABLE,
            exc=exc,
        )
    except Exception as exc:
        return build_internal_error_response(
            context,
            base_request.id,
            log_message="OpenCode session query JSON-RPC method failed",
            exc=exc,
        )

    try:
        if base_request.method == context.method_list_sessions:
            raw_items = _extract_raw_items(raw_result, kind="sessions")
        else:
            raw_items = _extract_raw_items(raw_result.payload, kind="messages")
    except ValueError as exc:
        logger.warning("Upstream OpenCode payload mismatch: %s", exc)
        return build_upstream_payload_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_PAYLOAD_ERROR,
            detail=str(exc),
        )

    if base_request.method == context.method_list_sessions:
        mapped: list[dict[str, Any]] = []
        for item in raw_items:
            task = _as_a2a_session_task(item)
            if task is not None:
                mapped.append(task)
        items: list[dict[str, Any]] = _apply_session_query_limit(mapped, limit=limit)
    else:
        assert session_id is not None
        mapped = []
        for item in raw_items:
            message = _as_a2a_message(session_id, item)
            if message is not None:
                mapped.append(message)
        items = mapped

    result: dict[str, Any] = {"items": items}
    if base_request.method == context.method_get_session_messages:
        result["next_cursor"] = raw_result.next_cursor
    return build_success_response(context, base_request.id, result)

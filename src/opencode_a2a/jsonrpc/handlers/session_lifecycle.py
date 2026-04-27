from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ...contracts.extensions import SESSION_QUERY_ERROR_BUSINESS_CODES
from ...invocation import call_with_supported_kwargs
from ...opencode_upstream_client import UpstreamContractError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error, session_not_found_error
from ..methods import (
    _as_a2a_message,
    _as_a2a_session_task,
    _extract_raw_items,
    _normalize_diff_items,
    _normalize_session_status_items,
    _normalize_session_summary,
    _normalize_todo_items,
)
from ..models import JSONRPCRequest
from .common import (
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


def _invalid_field_error(field: str, message: str) -> Any:
    return invalid_params_error(message, data={"type": "INVALID_FIELD", "field": field})


def _parse_directory_hint(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    raw_directory = params.get("directory")
    if raw_directory is None:
        return None, None
    if not isinstance(raw_directory, str):
        return None, context.error_response(
            request_id,
            _invalid_field_error("directory", "directory must be a string"),
        )
    normalized = raw_directory.strip()
    return normalized or None, None


def _parse_required_string(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
    *,
    field: str,
) -> tuple[str | None, Response | None]:
    value = params.get(field)
    if not isinstance(value, str) or not value.strip():
        return None, context.error_response(
            request_id,
            invalid_params_error(
                f"Missing required params.{field}",
                data={"type": "MISSING_FIELD", "field": field},
            ),
        )
    return value.strip(), None


def _parse_optional_string(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
    *,
    field: str,
) -> tuple[str | None, Response | None]:
    value = params.get(field)
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, context.error_response(
            request_id,
            _invalid_field_error(field, f"{field} must be a string"),
        )
    normalized = value.strip()
    return normalized or None, None


def _parse_fork_request(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[dict[str, Any], Response | None]:
    raw_request = params.get("request")
    if raw_request is None:
        return {}, None
    if not isinstance(raw_request, dict):
        return {}, context.error_response(
            request_id,
            _invalid_field_error("request", "params.request must be an object"),
        )
    unknown_fields_error = reject_unknown_fields(
        context,
        request_id,
        raw_request,
        allowed_fields={"messageID"},
        field_prefix="request.",
    )
    if unknown_fields_error is not None:
        return {}, unknown_fields_error
    message_id = raw_request.get("messageID")
    if message_id is None:
        return {}, None
    if not isinstance(message_id, str) or not message_id.strip():
        return {}, context.error_response(
            request_id,
            _invalid_field_error("request.messageID", "request.messageID must be a string"),
        )
    return {"messageID": message_id.strip()}, None


def _parse_summarize_request(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, Response | None]:
    raw_request = params.get("request")
    if raw_request is None:
        return None, None
    if not isinstance(raw_request, dict):
        return None, context.error_response(
            request_id,
            _invalid_field_error("request", "params.request must be an object"),
        )
    unknown_fields_error = reject_unknown_fields(
        context,
        request_id,
        raw_request,
        allowed_fields={"providerID", "modelID", "auto"},
        field_prefix="request.",
    )
    if unknown_fields_error is not None:
        return None, unknown_fields_error
    provider_id = raw_request.get("providerID")
    model_id = raw_request.get("modelID")
    auto = raw_request.get("auto")
    if provider_id is None and model_id is None and auto is None:
        return None, None
    if not isinstance(provider_id, str) or not provider_id.strip():
        return None, context.error_response(
            request_id,
            _invalid_field_error("request.providerID", "request.providerID must be a string"),
        )
    if not isinstance(model_id, str) or not model_id.strip():
        return None, context.error_response(
            request_id,
            _invalid_field_error("request.modelID", "request.modelID must be a string"),
        )
    request_payload: dict[str, Any] = {
        "providerID": provider_id.strip(),
        "modelID": model_id.strip(),
    }
    if auto is not None:
        if not isinstance(auto, bool):
            return None, context.error_response(
                request_id,
                _invalid_field_error("request.auto", "request.auto must be a boolean"),
            )
        request_payload["auto"] = auto
    return request_payload, None


def _parse_revert_request(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[dict[str, Any], Response | None]:
    raw_request = params.get("request")
    if not isinstance(raw_request, dict):
        return {}, context.error_response(
            request_id,
            _invalid_field_error("request", "params.request must be an object"),
        )
    unknown_fields_error = reject_unknown_fields(
        context,
        request_id,
        raw_request,
        allowed_fields={"messageID", "partID"},
        field_prefix="request.",
    )
    if unknown_fields_error is not None:
        return {}, unknown_fields_error
    message_id = raw_request.get("messageID")
    if not isinstance(message_id, str) or not message_id.strip():
        return {}, context.error_response(
            request_id,
            _invalid_field_error("request.messageID", "request.messageID must be a string"),
        )
    request_payload: dict[str, Any] = {"messageID": message_id.strip()}
    part_id = raw_request.get("partID")
    if part_id is not None:
        if not isinstance(part_id, str) or not part_id.strip():
            return {}, context.error_response(
                request_id,
                _invalid_field_error("request.partID", "request.partID must be a string"),
            )
        request_payload["partID"] = part_id.strip()
    return request_payload, None


async def handle_session_lifecycle_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    method = base_request.method
    allowed_fields = {"metadata", "directory"}
    session_id: str | None = None
    message_id: str | None = None
    fork_request: dict[str, Any] = {}
    summarize_request: dict[str, Any] | None = None
    revert_request: dict[str, Any] = {}

    if method in {
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
    }:
        allowed_fields.add("session_id")
    if method == context.method_get_session_diff:
        allowed_fields.add("message_id")
    if method == context.method_get_session_message:
        allowed_fields.add("message_id")
    if method in {
        context.method_fork_session,
        context.method_summarize_session,
        context.method_revert_session,
    }:
        allowed_fields.add("request")

    unknown_fields_error = reject_unknown_fields(
        context,
        base_request.id,
        params,
        allowed_fields=allowed_fields,
    )
    if unknown_fields_error is not None:
        return unknown_fields_error

    directory, directory_error = _parse_directory_hint(context, base_request.id, params)
    if directory_error is not None:
        return directory_error

    if "session_id" in allowed_fields:
        session_id, session_error = _parse_required_string(
            context,
            base_request.id,
            params,
            field="session_id",
        )
        if session_error is not None:
            return session_error

    if method in {context.method_get_session_diff, context.method_get_session_message}:
        message_id, message_error = _parse_optional_string(
            context,
            base_request.id,
            params,
            field="message_id",
        )
        if message_error is not None:
            return message_error
        if method == context.method_get_session_message and message_id is None:
            return context.error_response(
                base_request.id,
                invalid_params_error(
                    "Missing required params.message_id",
                    data={"type": "MISSING_FIELD", "field": "message_id"},
                ),
            )

    if method == context.method_fork_session:
        fork_request, fork_error = _parse_fork_request(context, base_request.id, params)
        if fork_error is not None:
            return fork_error
    elif method == context.method_summarize_session:
        summarize_request, summarize_error = _parse_summarize_request(
            context, base_request.id, params
        )
        if summarize_error is not None:
            return summarize_error
    elif method == context.method_revert_session:
        revert_request, revert_error = _parse_revert_request(context, base_request.id, params)
        if revert_error is not None:
            return revert_error

    resolved_directory, workspace_id, routing_error = resolve_routing_context(
        context,
        request_id=base_request.id,
        params=params,
        requested_directory=directory,
    )
    if routing_error is not None:
        return routing_error

    request_identity = getattr(request.state, "user_identity", None)
    identity = request_identity if isinstance(request_identity, str) else None
    mutating_methods = {
        context.method_fork_session,
        context.method_share_session,
        context.method_unshare_session,
        context.method_summarize_session,
        context.method_revert_session,
        context.method_unrevert_session,
    }

    claim_identity = identity if method in mutating_methods else None
    try:
        async with claim_session(
            context,
            identity=claim_identity,
            session_id=session_id,
            logger=logger,
        ) as session_claim:
            result: dict[str, Any]
            forked_session_id: str | None = None

            if method == context.method_session_status:
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.session_status,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"items": _normalize_session_status_items(raw_result)}
            elif method == context.method_get_session:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.get_session,
                    session_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                item = _as_a2a_session_task(raw_result)
                if item is None:
                    raise UpstreamContractError(
                        "OpenCode /session/{sessionID} response could not be mapped to A2A Task"
                    )
                result = {"item": item}
            elif method == context.method_get_session_children:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.list_child_sessions,
                    session_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                raw_items = _extract_raw_items(raw_result, kind="child sessions")
                result = {
                    "items": [
                        task
                        for item in raw_items
                        if (task := _as_a2a_session_task(item)) is not None
                    ]
                }
            elif method == context.method_get_session_todo:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.get_session_todo,
                    session_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"items": _normalize_todo_items(raw_result)}
            elif method == context.method_get_session_diff:
                assert session_id is not None
                query = {"messageID": message_id} if message_id else None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.get_session_diff,
                    session_id,
                    params=query,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"items": _normalize_diff_items(raw_result)}
            elif method == context.method_get_session_message:
                assert session_id is not None
                assert message_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.get_message,
                    session_id,
                    message_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                item = _as_a2a_message(session_id, raw_result)
                if item is None:
                    raise UpstreamContractError(
                        "OpenCode /session/{sessionID}/message/{messageID} response could not "
                        "be mapped to A2A Message"
                    )
                result = {"item": item}
            elif method == context.method_fork_session:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.fork_session,
                    session_id,
                    request=fork_request,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                item = _normalize_session_summary(raw_result)
                forked_session_id = item["id"]
                result = {"item": item}
            elif method == context.method_share_session:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.share_session,
                    session_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"item": _normalize_session_summary(raw_result)}
            elif method == context.method_summarize_session:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.summarize_session,
                    session_id,
                    request=summarize_request,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                if not isinstance(raw_result, bool):
                    raise ValueError("Upstream summarize response must be a boolean")
                result = {"ok": raw_result, "session_id": session_id}
            elif method == context.method_revert_session:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.revert_session,
                    session_id,
                    request=revert_request,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"item": _normalize_session_summary(raw_result)}
            elif method == context.method_unrevert_session:
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.unrevert_session,
                    session_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"item": _normalize_session_summary(raw_result)}
            else:
                assert method == context.method_unshare_session
                assert session_id is not None
                raw_result = await call_with_supported_kwargs(
                    context.upstream_client.unshare_session,
                    session_id,
                    directory=resolved_directory,
                    workspace_id=workspace_id,
                )
                result = {"item": _normalize_session_summary(raw_result)}

            await session_claim.finalize()
            if forked_session_id is not None and identity:
                await context.session_claim_finalize(
                    identity=identity, session_id=forked_session_id
                )
    except Exception as exc:

        def _session_not_found_response() -> Response:
            assert session_id is not None
            return context.error_response(
                base_request.id,
                session_not_found_error(ERR_SESSION_NOT_FOUND, session_id=session_id),
            )

        return build_upstream_exception_response(
            context,
            base_request.id,
            exc=exc,
            upstream_http_error_code=ERR_UPSTREAM_HTTP_ERROR,
            upstream_unreachable_error_code=ERR_UPSTREAM_UNREACHABLE,
            upstream_payload_error_code=ERR_UPSTREAM_PAYLOAD_ERROR,
            internal_log_message="OpenCode session lifecycle JSON-RPC method failed",
            method=method,
            session_id=session_id,
            on_not_found=_session_not_found_response if session_id is not None else None,
            on_permission_error=(
                lambda: build_session_forbidden_response(
                    context,
                    base_request.id,
                    session_id=session_id,
                )
            )
            if session_id is not None
            else None,
            payload_warning_message="Upstream OpenCode payload mismatch",
        )

    return build_success_response(context, base_request.id, result)

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ...auth import (
    CAPABILITY_WORKSPACE_MUTATION,
    request_has_capability,
)
from ...contracts.extensions import WORKSPACE_CONTROL_ERROR_BUSINESS_CODES
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error
from ..models import JSONRPCRequest
from .common import (
    build_authorization_forbidden_response,
    build_success_response,
    build_upstream_payload_error_response,
    invoke_upstream_or_error,
    reject_unknown_fields,
)

logger = logging.getLogger(__name__)

ERR_UPSTREAM_UNREACHABLE = WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_UPSTREAM_PAYLOAD_ERROR = WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["UPSTREAM_PAYLOAD_ERROR"]


def _invalid_field_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    field: str,
    message: str,
) -> Response:
    return context.error_response(
        request_id,
        invalid_params_error(message, data={"type": "INVALID_FIELD", "field": field}),
    )


def _missing_field_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    field: str,
    error_field: str | None = None,
) -> Response:
    return context.error_response(
        request_id,
        invalid_params_error(
            f"Missing required params.{field}",
            data={"type": "MISSING_FIELD", "field": error_field or field},
        ),
    )


def _parse_required_request_object(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[dict[str, Any] | None, Response | None]:
    value = params.get("request")
    if value is None:
        return None, _missing_field_response(context, request_id, field="request")
    if not isinstance(value, dict):
        return None, _invalid_field_response(
            context,
            request_id,
            field="request",
            message="params.request must be an object",
        )
    return dict(value), None


def _parse_workspace_id(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    raw_workspace_id = params.get("workspace_id")
    if not isinstance(raw_workspace_id, str) or not raw_workspace_id.strip():
        return None, _missing_field_response(context, request_id, field="workspace_id")
    return raw_workspace_id.strip(), None


def _validate_workspace_request(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    method: str,
    request: dict[str, Any],
) -> Response | None:
    if method == "create_workspace":
        allowed_fields = {"id", "type", "branch", "extra"}
        if "type" not in request:
            return _missing_field_response(
                context,
                request_id,
                field="request.type",
                error_field="request",
            )
        request_type = request.get("type")
        if not isinstance(request_type, str) or not request_type.strip():
            return _invalid_field_response(
                context,
                request_id,
                field="request.type",
                message="params.request.type must be a non-empty string",
            )
    elif method == "create_worktree":
        allowed_fields = {"name", "startCommand"}
    elif method in {"remove_worktree", "reset_worktree"}:
        allowed_fields = {"directory"}
        directory = request.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            return _invalid_field_response(
                context,
                request_id,
                field="request.directory",
                message="params.request.directory must be a non-empty string",
            )
    else:
        allowed_fields = set()

    unknown_fields_error = reject_unknown_fields(
        context,
        request_id,
        request,
        allowed_fields=allowed_fields,
        field_prefix="request.",
        message_prefix="Unsupported request fields",
    )
    if unknown_fields_error is not None:
        return unknown_fields_error

    for field in ("id", "type", "branch", "name", "startCommand", "directory"):
        if field not in request:
            continue
        value = request[field]
        if value is not None and not isinstance(value, str):
            return _invalid_field_response(
                context,
                request_id,
                field=f"request.{field}",
                message=f"params.request.{field} must be a string",
            )
    return None


def _validate_allowed_fields(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    method: str,
    params: dict[str, Any],
) -> Response | None:
    allowed_fields = {"metadata"}
    if method in {"create_workspace", "create_worktree", "remove_worktree", "reset_worktree"}:
        allowed_fields.add("request")
    if method == "remove_workspace":
        allowed_fields.add("workspace_id")

    return reject_unknown_fields(
        context,
        request_id,
        params,
        allowed_fields=allowed_fields,
    )


def _validate_response_payload(method: str, payload: Any) -> dict[str, Any]:
    if method in {"list_projects", "list_workspaces", "list_worktrees"}:
        if not isinstance(payload, list):
            raise ValueError("Upstream list response must be an array")
        return {"items": payload}
    if method in {"get_current_project", "create_workspace", "remove_workspace", "create_worktree"}:
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("Upstream item response must be an object or null")
        return {"item": payload}
    if method in {"remove_worktree", "reset_worktree"}:
        if not isinstance(payload, bool):
            raise ValueError("Upstream boolean response must be a boolean")
        return {"ok": payload}
    raise ValueError(f"Unsupported workspace control method: {method}")


async def handle_workspace_control_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    method_map: dict[str, str] = {
        context.method_list_projects: "list_projects",
        context.method_get_current_project: "get_current_project",
        context.method_list_workspaces: "list_workspaces",
        context.method_list_worktrees: "list_worktrees",
    }
    optional_methods = (
        (context.method_create_workspace, "create_workspace"),
        (context.method_remove_workspace, "remove_workspace"),
        (context.method_create_worktree, "create_worktree"),
        (context.method_remove_worktree, "remove_worktree"),
        (context.method_reset_worktree, "reset_worktree"),
    )
    for method_name, optional_method_key in optional_methods:
        if method_name is not None:
            method_map[method_name] = optional_method_key
    method_key: str | None = method_map.get(base_request.method)
    if method_key is None:
        return context.error_response(
            base_request.id,
            invalid_params_error(
                f"Unsupported method: {base_request.method}",
                data={"type": "INVALID_FIELD", "field": "method"},
            ),
        )

    if method_key in {
        "create_workspace",
        "remove_workspace",
        "create_worktree",
        "remove_worktree",
        "reset_worktree",
    } and not request_has_capability(request, CAPABILITY_WORKSPACE_MUTATION):
        credential_id = getattr(request.state, "user_credential_id", None)
        return build_authorization_forbidden_response(
            context,
            base_request.id,
            method=base_request.method,
            capability=CAPABILITY_WORKSPACE_MUTATION,
            credential_id=credential_id if isinstance(credential_id, str) else None,
            error_code=WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["AUTHORIZATION_FORBIDDEN"],
        )

    allowed_fields_error = _validate_allowed_fields(context, base_request.id, method_key, params)
    if allowed_fields_error is not None:
        return allowed_fields_error

    request_body: dict[str, Any] | None = None
    workspace_id: str | None = None
    if method_key == "remove_workspace":
        workspace_id, workspace_error = _parse_workspace_id(context, base_request.id, params)
        if workspace_error is not None:
            return workspace_error
    elif method_key in {
        "create_workspace",
        "create_worktree",
        "remove_worktree",
        "reset_worktree",
    }:
        request_body, request_error = _parse_required_request_object(
            context,
            base_request.id,
            params,
        )
        if request_error is not None:
            return request_error
        assert request_body is not None
        request_validation_error = _validate_workspace_request(
            context,
            base_request.id,
            method_key,
            request_body,
        )
        if request_validation_error is not None:
            return request_validation_error

    async def _invoke_workspace_method() -> Any:
        if method_key == "list_projects":
            return await context.upstream_client.list_projects()
        if method_key == "get_current_project":
            return await context.upstream_client.get_current_project()
        if method_key == "list_workspaces":
            return await context.upstream_client.list_workspaces()
        if method_key == "create_workspace":
            return await context.upstream_client.create_workspace(request_body or {})
        if method_key == "remove_workspace":
            assert workspace_id is not None
            return await context.upstream_client.remove_workspace(workspace_id)
        if method_key == "list_worktrees":
            return await context.upstream_client.list_worktrees()
        if method_key == "create_worktree":
            return await context.upstream_client.create_worktree(request_body or {})
        if method_key == "remove_worktree":
            return await context.upstream_client.remove_worktree(request_body or {})
        return await context.upstream_client.reset_worktree(request_body or {})

    raw_result, upstream_error = await invoke_upstream_or_error(
        context,
        base_request.id,
        invoke=_invoke_workspace_method,
        upstream_http_error_code=ERR_UPSTREAM_HTTP_ERROR,
        upstream_unreachable_error_code=ERR_UPSTREAM_UNREACHABLE,
        internal_log_message="OpenCode workspace control JSON-RPC method failed",
        method=base_request.method,
    )
    if upstream_error is not None:
        return upstream_error
    assert raw_result is not None

    try:
        result = _validate_response_payload(method_key, raw_result)
    except ValueError as exc:
        logger.warning("Upstream OpenCode workspace payload mismatch: %s", exc)
        return build_upstream_payload_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_PAYLOAD_ERROR,
            detail=str(exc),
            method=base_request.method,
        )

    return build_success_response(context, base_request.id, result)

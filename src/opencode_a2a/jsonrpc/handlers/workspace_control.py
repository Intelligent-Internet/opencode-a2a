from __future__ import annotations

import logging
from typing import Any

import httpx
from a2a.types import JSONRPCRequest
from starlette.requests import Request
from starlette.responses import Response

from ...contracts.extensions import WORKSPACE_CONTROL_ERROR_BUSINESS_CODES
from ...opencode_upstream_client import UpstreamConcurrencyLimitError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error
from .common import (
    build_internal_error_response,
    build_success_response,
    build_upstream_concurrency_error_response,
    build_upstream_http_error_response,
    build_upstream_payload_error_response,
    build_upstream_unreachable_error_response,
)

logger = logging.getLogger(__name__)

ERR_UPSTREAM_UNREACHABLE = WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_UPSTREAM_PAYLOAD_ERROR = WORKSPACE_CONTROL_ERROR_BUSINESS_CODES["UPSTREAM_PAYLOAD_ERROR"]


def _parse_optional_request_object(
    params: dict[str, Any],
    *,
    required: bool,
) -> dict[str, Any] | None:
    value = params.get("request")
    if value is None:
        if required:
            raise ValueError("Missing required params.request")
        return None
    if not isinstance(value, dict):
        raise TypeError("params.request must be an object")
    return dict(value)


def _parse_workspace_id(params: dict[str, Any]) -> str:
    raw_workspace_id = params.get("workspace_id")
    if not isinstance(raw_workspace_id, str) or not raw_workspace_id.strip():
        raise ValueError("Missing required params.workspace_id")
    return raw_workspace_id.strip()


def _validate_workspace_request(method: str, request: dict[str, Any]) -> None:
    if method == "create_workspace":
        allowed_fields = {"id", "type", "branch", "extra"}
        if "type" not in request:
            raise ValueError("Missing required params.request.type")
        request_type = request.get("type")
        if not isinstance(request_type, str) or not request_type.strip():
            raise TypeError("params.request.type must be a non-empty string")
    elif method == "create_worktree":
        allowed_fields = {"name", "startCommand"}
    elif method in {"remove_worktree", "reset_worktree"}:
        allowed_fields = {"directory"}
        directory = request.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            raise TypeError("params.request.directory must be a non-empty string")
    else:
        allowed_fields = set()

    unknown_fields = sorted(set(request) - allowed_fields)
    if unknown_fields:
        raise ValueError(
            "Unsupported request fields: "
            + ", ".join(f"request.{field}" for field in unknown_fields)
        )

    for field in ("id", "type", "branch", "name", "startCommand", "directory"):
        if field not in request:
            continue
        value = request[field]
        if value is not None and not isinstance(value, str):
            raise TypeError(f"params.request.{field} must be a string")


def _validate_allowed_fields(
    method: str,
    params: dict[str, Any],
) -> None:
    allowed_fields = {"metadata"}
    if method in {"create_workspace", "create_worktree", "remove_worktree", "reset_worktree"}:
        allowed_fields.add("request")
    if method == "remove_workspace":
        allowed_fields.add("workspace_id")

    unknown_fields = sorted(set(params) - allowed_fields)
    if unknown_fields:
        raise ValueError("Unsupported fields: " + ", ".join(unknown_fields))


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
    del request

    method_map = {
        context.method_list_projects: "list_projects",
        context.method_get_current_project: "get_current_project",
        context.method_list_workspaces: "list_workspaces",
        context.method_create_workspace: "create_workspace",
        context.method_remove_workspace: "remove_workspace",
        context.method_list_worktrees: "list_worktrees",
        context.method_create_worktree: "create_worktree",
        context.method_remove_worktree: "remove_worktree",
        context.method_reset_worktree: "reset_worktree",
    }
    method_key = method_map.get(base_request.method)
    if method_key is None:
        return context.error_response(
            base_request.id,
            invalid_params_error(
                f"Unsupported method: {base_request.method}",
                data={"type": "INVALID_FIELD", "field": "method"},
            ),
        )

    try:
        _validate_allowed_fields(method_key, params)
        request_body: dict[str, Any] | None = None
        workspace_id: str | None = None
        if method_key == "remove_workspace":
            workspace_id = _parse_workspace_id(params)
        elif method_key in {
            "create_workspace",
            "create_worktree",
            "remove_worktree",
            "reset_worktree",
        }:
            request_body = _parse_optional_request_object(
                params,
                required=True,
            )
            assert request_body is not None
            _validate_workspace_request(method_key, request_body)
    except ValueError as exc:
        field = "workspace_id" if "workspace_id" in str(exc) else "request"
        return context.error_response(
            base_request.id,
            invalid_params_error(str(exc), data={"type": "INVALID_FIELD", "field": field}),
        )
    except TypeError as exc:
        return context.error_response(
            base_request.id,
            invalid_params_error(str(exc), data={"type": "INVALID_FIELD"}),
        )

    try:
        if method_key == "list_projects":
            raw_result = await context.upstream_client.list_projects()
        elif method_key == "get_current_project":
            raw_result = await context.upstream_client.get_current_project()
        elif method_key == "list_workspaces":
            raw_result = await context.upstream_client.list_workspaces()
        elif method_key == "create_workspace":
            raw_result = await context.upstream_client.create_workspace(request_body or {})
        elif method_key == "remove_workspace":
            assert workspace_id is not None
            raw_result = await context.upstream_client.remove_workspace(workspace_id)
        elif method_key == "list_worktrees":
            raw_result = await context.upstream_client.list_worktrees()
        elif method_key == "create_worktree":
            raw_result = await context.upstream_client.create_worktree(request_body or {})
        elif method_key == "remove_worktree":
            raw_result = await context.upstream_client.remove_worktree(request_body or {})
        else:
            raw_result = await context.upstream_client.reset_worktree(request_body or {})
    except httpx.HTTPStatusError as exc:
        return build_upstream_http_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_HTTP_ERROR,
            upstream_status=exc.response.status_code,
            method=base_request.method,
        )
    except httpx.HTTPError:
        return build_upstream_unreachable_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_UNREACHABLE,
            method=base_request.method,
        )
    except UpstreamConcurrencyLimitError as exc:
        return build_upstream_concurrency_error_response(
            context,
            base_request.id,
            ERR_UPSTREAM_UNREACHABLE,
            exc=exc,
            method=base_request.method,
        )
    except Exception as exc:
        return build_internal_error_response(
            context,
            base_request.id,
            log_message="OpenCode workspace control JSON-RPC method failed",
            exc=exc,
        )

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

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ..dispatch import ExtensionHandlerContext
from ..models import JSONRPCRequest
from .common import build_internal_error_response, build_success_response, reject_unknown_fields


async def handle_interrupt_query_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    unknown_fields_error = reject_unknown_fields(
        context,
        base_request.id,
        params,
        allowed_fields=set(),
    )
    if unknown_fields_error is not None:
        return unknown_fields_error

    request_identity = getattr(request.state, "user_identity", None)
    identity = request_identity.strip() if isinstance(request_identity, str) else ""
    if not identity:
        return build_success_response(context, base_request.id, {"items": []})

    try:
        if base_request.method == context.method_list_permissions:
            items = await context.upstream_client.list_permission_requests(identity=identity)
        else:
            items = await context.upstream_client.list_question_requests(identity=identity)
    except Exception as exc:
        return build_internal_error_response(
            context,
            base_request.id,
            log_message="Interrupt recovery JSON-RPC method failed",
            exc=exc,
        )

    return build_success_response(
        context,
        base_request.id,
        {
            "items": [
                {
                    "request_id": item.request_id,
                    "session_id": item.session_id,
                    "interrupt_type": item.interrupt_type,
                    "task_id": item.task_id,
                    "context_id": item.context_id,
                    "details": dict(item.details) if isinstance(item.details, dict) else None,
                    "expires_at": item.expires_at,
                }
                for item in items
            ]
        },
    )

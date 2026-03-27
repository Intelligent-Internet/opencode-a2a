from __future__ import annotations

from typing import Any

from a2a.types import JSONRPCRequest
from starlette.requests import Request
from starlette.responses import Response

from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error
from .common import build_internal_error_response, build_success_response


def _binding_to_result_item(binding: Any) -> dict[str, Any]:
    return {
        "request_id": binding.request_id,
        "session_id": binding.session_id,
        "interrupt_type": binding.interrupt_type,
        "task_id": binding.task_id,
        "context_id": binding.context_id,
        "details": dict(binding.details) if isinstance(binding.details, dict) else None,
        "expires_at": binding.expires_at,
    }


async def handle_interrupt_query_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    unknown_fields = sorted(params)
    if unknown_fields:
        return context.error_response(
            base_request.id,
            invalid_params_error(
                f"Unsupported fields: {', '.join(unknown_fields)}",
                data={"type": "INVALID_FIELD", "fields": unknown_fields},
            ),
        )

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
        {"items": [_binding_to_result_item(item) for item in items]},
    )

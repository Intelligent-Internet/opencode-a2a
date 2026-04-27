from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ...contracts.extensions import PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES
from ...invocation import call_with_supported_kwargs
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error
from ..methods import (
    _extract_provider_catalog,
    _normalize_model_summaries,
    _normalize_provider_summaries,
)
from ..models import JSONRPCRequest
from .common import (
    build_success_response,
    build_upstream_payload_error_response,
    invoke_upstream_or_error,
    reject_unknown_fields,
    resolve_routing_context,
)

logger = logging.getLogger(__name__)

ERR_DISCOVERY_UPSTREAM_UNREACHABLE = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_DISCOVERY_UPSTREAM_HTTP_ERROR = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_DISCOVERY_UPSTREAM_PAYLOAD_ERROR = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES[
    "UPSTREAM_PAYLOAD_ERROR"
]


async def handle_provider_discovery_request(
    context: ExtensionHandlerContext,
    base_request: JSONRPCRequest,
    params: dict[str, Any],
    request: Request,
) -> Response:
    del request
    allowed_fields = {"metadata"}
    if base_request.method == context.method_list_models:
        allowed_fields.add("provider_id")
    unknown_fields_error = reject_unknown_fields(
        context,
        base_request.id,
        params,
        allowed_fields=allowed_fields,
        field_prefix="params.",
        message_prefix="Unsupported params fields",
    )
    if unknown_fields_error is not None:
        return unknown_fields_error

    provider_id: str | None = None
    if base_request.method == context.method_list_models:
        raw_provider_id = params.get("provider_id")
        if raw_provider_id is not None:
            if not isinstance(raw_provider_id, str) or not raw_provider_id.strip():
                return context.error_response(
                    base_request.id,
                    invalid_params_error(
                        "provider_id must be a non-empty string",
                        data={"type": "INVALID_FIELD", "field": "provider_id"},
                    ),
                )
            provider_id = raw_provider_id.strip()

    directory, workspace_id, routing_error = resolve_routing_context(
        context,
        request_id=base_request.id,
        params=params,
    )
    if routing_error is not None:
        return routing_error

    raw_result, upstream_error = await invoke_upstream_or_error(
        context,
        base_request.id,
        invoke=lambda: call_with_supported_kwargs(
            context.upstream_client.list_provider_catalog,
            directory=directory,
            workspace_id=workspace_id,
        ),
        upstream_http_error_code=ERR_DISCOVERY_UPSTREAM_HTTP_ERROR,
        upstream_unreachable_error_code=ERR_DISCOVERY_UPSTREAM_UNREACHABLE,
        internal_log_message="OpenCode provider discovery JSON-RPC method failed",
        method=base_request.method,
    )
    if upstream_error is not None:
        return upstream_error
    assert raw_result is not None

    try:
        raw_providers, default_by_provider, connected = _extract_provider_catalog(raw_result)
        if base_request.method == context.method_list_providers:
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
        return build_upstream_payload_error_response(
            context,
            base_request.id,
            ERR_DISCOVERY_UPSTREAM_PAYLOAD_ERROR,
            detail=str(exc),
            method=base_request.method,
        )

    return build_success_response(
        context,
        base_request.id,
        {
            "items": items,
            "default_by_provider": default_by_provider,
            "connected": connected,
        },
    )

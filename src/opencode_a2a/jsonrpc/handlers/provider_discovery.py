from __future__ import annotations

import logging
from typing import Any

import httpx
from a2a.types import JSONRPCRequest
from starlette.requests import Request
from starlette.responses import Response

from ...contracts.extensions import PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES
from ...opencode_upstream_client import UpstreamConcurrencyLimitError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import invalid_params_error
from ..methods import (
    _extract_provider_catalog,
    _normalize_model_summaries,
    _normalize_provider_summaries,
)
from .common import (
    build_internal_error_response,
    build_success_response,
    build_upstream_concurrency_error_response,
    build_upstream_http_error_response,
    build_upstream_payload_error_response,
    build_upstream_unreachable_error_response,
    resolve_directory,
)

logger = logging.getLogger(__name__)

ERR_DISCOVERY_UPSTREAM_UNREACHABLE = PROVIDER_DISCOVERY_ERROR_BUSINESS_CODES[
    "UPSTREAM_UNREACHABLE"
]
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
    unknown_fields = sorted(set(params) - allowed_fields)
    if unknown_fields:
        prefixed_fields = [f"params.{field}" for field in unknown_fields]
        return context.error_response(
            base_request.id,
            invalid_params_error(
                f"Unsupported params fields: {', '.join(prefixed_fields)}",
                data={"type": "INVALID_FIELD", "fields": prefixed_fields},
            ),
        )

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

    directory, directory_error = resolve_directory(
        context,
        request_id=base_request.id,
        params=params,
    )
    if directory_error is not None:
        return directory_error

    try:
        raw_result = await context.upstream_client.list_provider_catalog(directory=directory)
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        return build_upstream_http_error_response(
            context,
            base_request.id,
            ERR_DISCOVERY_UPSTREAM_HTTP_ERROR,
            upstream_status=upstream_status,
            method=base_request.method,
        )
    except httpx.HTTPError:
        return build_upstream_unreachable_error_response(
            context,
            base_request.id,
            ERR_DISCOVERY_UPSTREAM_UNREACHABLE,
            method=base_request.method,
        )
    except UpstreamConcurrencyLimitError as exc:
        return build_upstream_concurrency_error_response(
            context,
            base_request.id,
            ERR_DISCOVERY_UPSTREAM_UNREACHABLE,
            exc=exc,
            method=base_request.method,
        )
    except Exception as exc:
        return build_internal_error_response(
            context,
            base_request.id,
            log_message="OpenCode provider discovery JSON-RPC method failed",
            exc=exc,
        )

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

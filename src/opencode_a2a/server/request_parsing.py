from __future__ import annotations

import json
import logging

from fastapi.responses import JSONResponse

from ..a2a_protocol import V1_JSONRPC_METHOD_TO_LEGACY_METHOD
from ..contracts.extensions import (
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_METHODS,
    SESSION_METHODS,
    WORKSPACE_CONTROL_METHODS,
)
from ..jsonrpc.error_responses import build_http_error_body

logger = logging.getLogger(__name__)

_V1_JSONRPC_METHOD_ALIASES = dict(V1_JSONRPC_METHOD_TO_LEGACY_METHOD)


def _parse_json_body(body_bytes: bytes) -> dict | None:
    try:
        payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _detect_sensitive_extension_method(payload: dict | None) -> str | None:
    if payload is None:
        return None
    method = payload.get("method")
    if not isinstance(method, str):
        return None
    sensitive_methods = (
        set(SESSION_METHODS.values())
        | set(INTERRUPT_CALLBACK_METHODS.values())
        | set(INTERRUPT_RECOVERY_METHODS.values())
        | set(WORKSPACE_CONTROL_METHODS.values())
    )
    if method in sensitive_methods:
        return method
    return None


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _normalize_content_type(value: str | None) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0].strip().lower()


def _is_json_content_type(content_type: str) -> bool:
    if not content_type:
        return False
    if content_type == "application/json":
        return True
    return content_type.endswith("+json")


def _decode_payload_preview(body: bytes, *, limit: int) -> str:
    if limit > 0 and len(body) > limit:
        preview = body[:limit].decode("utf-8", errors="replace")
        return f"{preview}...[truncated]"
    return body.decode("utf-8", errors="replace")


def _looks_like_jsonrpc_message_payload(payload: dict | None) -> bool:
    if payload is None:
        return False
    message = payload.get("message")
    if not isinstance(message, dict):
        return False
    if "parts" in message:
        return True
    role = message.get("role")
    return isinstance(role, str) and role in {"user", "agent"}


def _looks_like_jsonrpc_envelope(payload: dict | None) -> bool:
    if payload is None:
        return False
    method = payload.get("method")
    version = payload.get("jsonrpc")
    return isinstance(method, str) and isinstance(version, str)


def _normalize_v1_jsonrpc_method_alias(
    payload: dict | None, *, protocol_version: str
) -> dict | None:
    if payload is None or protocol_version != "1.0":
        return payload
    method = payload.get("method")
    if not isinstance(method, str):
        return payload
    canonical_method = _V1_JSONRPC_METHOD_ALIASES.get(method)
    if canonical_method is None or canonical_method == method:
        return payload
    normalized_payload = dict(payload)
    normalized_payload["method"] = canonical_method
    return normalized_payload


class _RequestBodyTooLargeError(Exception):
    def __init__(self, *, limit: int, actual_size: int) -> None:
        super().__init__("Request body too large")
        self.limit = limit
        self.actual_size = actual_size


def _request_body_too_large_response(
    *,
    path: str,
    method: str,
    error: _RequestBodyTooLargeError,
    protocol_version: str = "0.3",
) -> JSONResponse:
    logger.warning(
        "A2A request %s %s rejected: body_size=%s exceeds max_request_body_bytes=%s",
        method,
        path,
        error.actual_size,
        error.limit,
    )
    return JSONResponse(
        build_http_error_body(
            protocol_version=protocol_version,
            status_code=413,
            status="RESOURCE_EXHAUSTED",
            message="Request body too large",
            legacy_payload={"error": "Request body too large", "max_bytes": error.limit},
            reason="REQUEST_BODY_TOO_LARGE",
            metadata={"max_bytes": error.limit, "actual_size": error.actual_size},
        ),
        status_code=413,
    )

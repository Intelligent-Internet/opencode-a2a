from __future__ import annotations

import json
import logging

from a2a.types import SendMessageRequest
from fastapi.responses import JSONResponse

from ..contracts.extensions import (
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_METHODS,
    SESSION_METHODS,
    WORKSPACE_CONTROL_METHODS,
)
from ..jsonrpc.error_responses import build_http_error_body

logger = logging.getLogger(__name__)


def validate_send_message_request(request: SendMessageRequest) -> None:
    """Validate required A2A Message fields omitted by proto3 parsing."""
    if not request.HasField("message"):
        raise ValueError("params.message is required")

    message = request.message
    if not message.message_id.strip():
        raise ValueError("params.message.messageId is required")
    if message.role == 0:
        raise ValueError("params.message.role is required")
    if not message.parts:
        raise ValueError("params.message.parts must contain at least one part")
    for index, part in enumerate(message.parts):
        if part.WhichOneof("content") is None:
            raise ValueError(f"params.message.parts[{index}] must contain content")


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


def _looks_like_jsonrpc_envelope(payload: dict | None) -> bool:
    if payload is None:
        return False
    method = payload.get("method")
    version = payload.get("jsonrpc")
    return isinstance(method, str) and isinstance(version, str)


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
            status_code=413,
            status="RESOURCE_EXHAUSTED",
            message="Request body too large",
            reason="REQUEST_BODY_TOO_LARGE",
            metadata={"max_bytes": error.limit, "actual_size": error.actual_size},
        ),
        status_code=413,
    )

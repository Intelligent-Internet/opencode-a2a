"""Stable tool error mapping for execution-layer synthetic tool calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONRPCError

from ..client.error_mapping import map_http_error, map_jsonrpc_error
from ..client.errors import (
    A2AAgentUnavailableError,
    A2AAuthenticationError,
    A2AClientError,
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2APermissionDeniedError,
    A2ATimeoutError,
    A2AUnsupportedOperationError,
)


@dataclass(frozen=True)
class ToolErrorPayload:
    error: str
    error_code: str
    error_meta: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.error,
            "error_code": self.error_code,
        }
        if self.error_meta:
            payload["error_meta"] = self.error_meta
        return payload


def build_tool_error(
    *,
    error_code: str,
    error: str,
    error_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ToolErrorPayload(error=error, error_code=error_code, error_meta=error_meta).as_dict()


def map_a2a_tool_exception(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, A2AClientHTTPError):
        return map_a2a_tool_exception(map_http_error("message/send", exc))
    if isinstance(exc, A2AClientJSONRPCError):
        return map_a2a_tool_exception(map_jsonrpc_error(exc))
    if isinstance(exc, A2AAuthenticationError):
        return _build_client_error_payload(
            exc,
            error_code="a2a_peer_auth_failed",
            error="Authentication failed when calling remote A2A peer",
        )
    if isinstance(exc, A2APermissionDeniedError):
        return _build_client_error_payload(
            exc,
            error_code="a2a_peer_permission_denied",
            error="Permission denied when calling remote A2A peer",
        )
    if isinstance(exc, (A2ATimeoutError, httpx.TimeoutException)):
        return build_tool_error(
            error_code="a2a_timeout",
            error="Remote A2A peer timed out",
        )
    if isinstance(exc, A2AUnsupportedOperationError):
        return _build_client_error_payload(
            exc,
            error_code="a2a_unsupported_operation",
            error="Remote A2A peer does not support the requested operation",
        )
    if isinstance(exc, A2AClientResetRequiredError):
        return _build_client_error_payload(
            exc,
            error_code="a2a_retryable_unavailable",
            error="Remote A2A peer is temporarily unavailable and should be retried with a fresh client",
        )
    if isinstance(exc, A2APeerProtocolError):
        mapped_code = "a2a_invalid_agent_card" if exc.error_code == "invalid_agent_card" else "a2a_peer_protocol_error"
        mapped_error = (
            "Remote A2A peer returned an invalid agent card payload"
            if exc.error_code == "invalid_agent_card"
            else "Remote A2A peer returned an invalid protocol payload"
        )
        return _build_client_error_payload(
            exc,
            error_code=mapped_code,
            error=mapped_error,
        )
    if isinstance(exc, (A2AAgentUnavailableError, httpx.TransportError)):
        error_meta = None
        if isinstance(exc, A2AClientError):
            error_meta = _build_client_error_meta(exc)
        return build_tool_error(
            error_code="a2a_unavailable",
            error="Remote A2A peer is unavailable",
            error_meta=error_meta,
        )
    return build_tool_error(
        error_code="a2a_call_failed",
        error="Remote A2A call failed",
    )


def _build_client_error_payload(
    exc: A2AClientError,
    *,
    error_code: str,
    error: str,
) -> dict[str, Any]:
    return build_tool_error(
        error_code=error_code,
        error=error,
        error_meta=_build_client_error_meta(exc),
    )


def _build_client_error_meta(exc: A2AClientError) -> dict[str, Any] | None:
    error_meta: dict[str, Any] = {}
    if exc.error_code:
        error_meta["client_error_code"] = exc.error_code
    if exc.http_status is not None:
        error_meta["http_status"] = exc.http_status
    if exc.code is not None:
        error_meta["rpc_code"] = exc.code
    return error_meta or None


__all__ = ["build_tool_error", "map_a2a_tool_exception"]

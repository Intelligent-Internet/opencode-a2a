"""Centralized error mapping for outbound A2A client operations."""

from __future__ import annotations

import httpx
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONError, A2AClientJSONRPCError

from .errors import (
    A2AAgentUnavailableError,
    A2AAuthenticationError,
    A2AClientError,
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2APermissionDeniedError,
    A2ATimeoutError,
    A2AUnsupportedOperationError,
)


def _attach_http_status(error: A2AClientError, status: int | None) -> A2AClientError:
    error.http_status = status
    return error


def _extract_jsonrpc_error_payload(
    exc: A2AClientJSONRPCError,
) -> tuple[str | None, int | None, object]:
    error = getattr(exc, "error", None)
    if error is None:
        return None, None, None
    return (
        getattr(error, "message", None),
        getattr(error, "code", None),
        getattr(error, "data", None),
    )


def map_jsonrpc_error(exc: A2AClientJSONRPCError) -> A2AClientError:
    _message, code, data = _extract_jsonrpc_error_payload(exc)
    if code == -32601:
        parsed_error = A2AUnsupportedOperationError(
            "Remote A2A peer does not support the requested operation"
        )
        parsed_error.error_code = "method_not_supported"
        parsed_error.code = code
        parsed_error.data = data
        return parsed_error
    if code == -32602:
        return A2APeerProtocolError(
            "Remote A2A peer rejected the request payload",
            error_code="invalid_params",
            rpc_code=code,
            data=data,
        )
    if code == -32603:
        reset_required = A2AClientResetRequiredError(
            "Remote A2A peer entered an unstable state and requires a fresh client session"
        )
        reset_required.code = code
        reset_required.data = data
        return reset_required
    return A2APeerProtocolError(
        "Remote A2A peer returned a protocol error",
        error_code="peer_protocol_error",
        rpc_code=code,
        data=data,
    )


def map_http_error(operation: str, exc: A2AClientHTTPError) -> A2AClientError:
    status = exc.status_code
    if status == 401:
        return _attach_http_status(
            A2AAuthenticationError(
                f"Remote A2A peer rejected {operation} due to authentication failure"
            ),
            status,
        )
    if status == 403:
        return _attach_http_status(
            A2APermissionDeniedError(
                f"Remote A2A peer rejected {operation} due to insufficient permissions"
            ),
            status,
        )
    if status in {404, 405, 409, 501}:
        return _attach_http_status(
            A2AUnsupportedOperationError(
                f"Remote A2A peer does not support {operation}"
            ),
            status,
        )
    if status == 408:
        return _attach_http_status(
            A2ATimeoutError(f"Remote A2A peer timed out during {operation}"),
            status,
        )
    if status in {429, 502, 503, 504}:
        return _attach_http_status(
            A2AClientResetRequiredError(
                f"Remote A2A peer is temporarily unstable during {operation}"
            ),
            status,
        )
    return _attach_http_status(
        A2AAgentUnavailableError(f"Remote A2A peer is unavailable for {operation}"),
        status,
    )


def map_transport_error(
    operation: str,
    exc: httpx.TimeoutException | httpx.TransportError,
) -> A2AClientError:
    if isinstance(exc, httpx.TimeoutException):
        return A2ATimeoutError(f"Remote A2A peer timed out during {operation}")
    return A2AAgentUnavailableError(f"Remote A2A peer is unreachable for {operation}")


def map_operation_error(
    operation: str,
    exc: A2AClientHTTPError | A2AClientJSONRPCError | httpx.TimeoutException | httpx.TransportError,
) -> A2AClientError:
    if isinstance(exc, A2AClientHTTPError):
        return map_http_error(operation, exc)
    if isinstance(exc, A2AClientJSONRPCError):
        return map_jsonrpc_error(exc)
    return map_transport_error(operation, exc)


def map_agent_card_error(
    exc: A2AClientHTTPError | A2AClientJSONError | httpx.TimeoutException | httpx.TransportError,
) -> A2AClientError:
    if isinstance(exc, A2AClientHTTPError):
        return map_http_error("agent-card/fetch", exc)
    if isinstance(exc, A2AClientJSONError):
        return A2APeerProtocolError(
            "Remote A2A peer returned an invalid agent card payload",
            error_code="invalid_agent_card",
        )
    return map_transport_error("agent-card/fetch", exc)


__all__ = [
    "map_agent_card_error",
    "map_http_error",
    "map_jsonrpc_error",
    "map_operation_error",
    "map_transport_error",
]

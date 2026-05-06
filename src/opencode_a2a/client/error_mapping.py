"""Centralized error mapping for outbound A2A client operations."""

from __future__ import annotations

import re

import httpx
from a2a.client.errors import (
    A2AClientError as SDKClientError,
)
from a2a.client.errors import (
    A2AClientTimeoutError,
    AgentCardResolutionError,
)
from a2a.utils.errors import (
    A2AError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)

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

_HTTP_STATUS_RE = re.compile(r"HTTP Error (\d+):")


def _attach_http_status(error: A2AClientError, status: int | None) -> A2AClientError:
    error.http_status = status
    return error


def _extract_http_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    match = _HTTP_STATUS_RE.search(str(exc))
    if match is None:
        return None
    return int(match.group(1))


def _extract_jsonrpc_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    error = getattr(response, "error", None)
    code = getattr(error, "code", None)
    return code if isinstance(code, int) else None


def _extract_jsonrpc_data(exc: Exception) -> object | None:
    response = getattr(exc, "response", None)
    error = getattr(response, "error", None)
    return getattr(error, "data", None)


def map_a2a_error(exc: A2AError) -> A2AClientError:
    if isinstance(exc, TaskNotFoundError):
        unsupported = A2AUnsupportedOperationError("Remote A2A peer could not find the task")
        unsupported.error_code = "task_not_found"
        return unsupported
    if isinstance(exc, (TaskNotCancelableError, UnsupportedOperationError, MethodNotFoundError)):
        unsupported = A2AUnsupportedOperationError(
            "Remote A2A peer does not support the requested operation"
        )
        unsupported.error_code = "method_not_supported"
        return unsupported
    if isinstance(exc, (InvalidParamsError, InvalidRequestError)):
        return A2APeerProtocolError(
            "Remote A2A peer rejected the request payload",
            error_code="invalid_params",
            data=exc.data,
        )
    if isinstance(exc, VersionNotSupportedError):
        unsupported = A2AUnsupportedOperationError("Remote A2A peer rejected the requested version")
        unsupported.error_code = "version_not_supported"
        unsupported.data = exc.data
        return unsupported
    return A2APeerProtocolError(
        "Remote A2A peer returned a protocol error",
        error_code="peer_protocol_error",
        data=exc.data,
    )


def map_client_error(operation: str, exc: SDKClientError) -> A2AClientError:
    if isinstance(exc, A2AClientTimeoutError):
        return A2ATimeoutError(f"Remote A2A peer timed out during {operation}")

    status = _extract_http_status(exc)
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
            A2AUnsupportedOperationError(f"Remote A2A peer does not support {operation}"),
            status,
        )
    if status == 408:
        return _attach_http_status(
            A2ATimeoutError(f"Remote A2A peer timed out during {operation}"),
            status,
        )
    if status in {429, 502, 503, 504}:
        reset_required = A2AClientResetRequiredError(
            f"Remote A2A peer is temporarily unstable during {operation}"
        )
        reset_required.http_status = status
        return reset_required
    if status is not None:
        return _attach_http_status(
            A2AAgentUnavailableError(f"Remote A2A peer is unavailable for {operation}"),
            status,
        )
    return A2APeerProtocolError(
        f"Remote A2A peer returned an invalid client error during {operation}",
        error_code="invalid_client_error",
    )


def map_http_error(operation: str, exc: Exception) -> A2AClientError:
    status = _extract_http_status(exc)
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
            A2AUnsupportedOperationError(f"Remote A2A peer does not support {operation}"),
            status,
        )
    if status == 408:
        return _attach_http_status(
            A2ATimeoutError(f"Remote A2A peer timed out during {operation}"),
            status,
        )
    if status in {429, 502, 503, 504}:
        reset_required = A2AClientResetRequiredError(
            f"Remote A2A peer is temporarily unstable during {operation}"
        )
        reset_required.http_status = status
        return reset_required
    if status is not None:
        return _attach_http_status(
            A2AAgentUnavailableError(f"Remote A2A peer is unavailable for {operation}"),
            status,
        )
    return A2APeerProtocolError(
        f"Remote A2A peer returned an invalid client error during {operation}",
        error_code="invalid_client_error",
    )


def map_jsonrpc_error(exc: Exception) -> A2AClientError:
    code = _extract_jsonrpc_code(exc)
    data = _extract_jsonrpc_data(exc)
    if code == -32601:
        unsupported = A2AUnsupportedOperationError(
            "Remote A2A peer does not support the requested operation"
        )
        unsupported.error_code = "method_not_supported"
        return unsupported
    if code == -32602:
        return A2APeerProtocolError(
            "Remote A2A peer rejected the request payload",
            error_code="invalid_params",
            rpc_code=code,
            data=data if isinstance(data, dict) else None,
        )
    if code == -32603:
        return A2AClientResetRequiredError("Remote A2A peer is temporarily unstable during call")
    return A2APeerProtocolError(
        "Remote A2A peer returned a protocol error",
        error_code="peer_protocol_error",
        rpc_code=code,
        data=data if isinstance(data, dict) else None,
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
    exc: A2AError | SDKClientError | httpx.TimeoutException | httpx.TransportError,
) -> A2AClientError:
    if _extract_jsonrpc_code(exc) is not None:
        return map_jsonrpc_error(exc)
    if _extract_http_status(exc) is not None and not isinstance(exc, httpx.TimeoutException):
        return map_http_error(operation, exc)
    if isinstance(exc, SDKClientError):
        return map_client_error(operation, exc)
    if isinstance(exc, A2AError):
        return map_a2a_error(exc)
    return map_transport_error(operation, exc)


def map_agent_card_error(
    exc: AgentCardResolutionError | httpx.TimeoutException | httpx.TransportError | Exception,
) -> A2AClientError:
    if isinstance(exc, AgentCardResolutionError):
        if exc.status_code is not None:
            return map_http_error("agent-card/fetch", exc)
        return A2APeerProtocolError(
            "Remote A2A peer returned an invalid agent card payload",
            error_code="invalid_agent_card",
        )
    if isinstance(exc, SDKClientError):
        status = _extract_http_status(exc)
        if status is not None:
            return map_http_error("agent-card/fetch", exc)
        return A2APeerProtocolError(
            "Remote A2A peer returned an invalid agent card payload",
            error_code="invalid_agent_card",
        )
    if _extract_http_status(exc) is not None:
        return map_http_error("agent-card/fetch", exc)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return map_transport_error("agent-card/fetch", exc)
    return A2AAgentUnavailableError("Remote A2A peer is unreachable for agent-card/fetch")

"""Error definitions for client initialization and runtime delegation."""

from __future__ import annotations


class A2AClientError(RuntimeError):
    """Base error for opencode-a2a A2A client wrapper."""

    error_code = "client_error"
    code: int | None = None
    data: object | None = None
    http_status: int | None = None


class A2AAgentUnavailableError(A2AClientError):
    """Raised when a remote A2A peer cannot be reached."""

    error_code = "agent_unavailable"


class A2AAuthenticationError(A2AClientError):
    """Raised when a remote A2A peer rejects authentication."""

    error_code = "authentication_failed"


class A2APermissionDeniedError(A2AClientError):
    """Raised when a remote A2A peer rejects authorization."""

    error_code = "permission_denied"


class A2AClientResetRequiredError(A2AAgentUnavailableError):
    """Raised when the cached transport should be rebuilt."""

    error_code = "reset_required"


class A2ATimeoutError(A2AAgentUnavailableError):
    """Raised when an outbound call to a remote A2A peer times out."""

    error_code = "timeout"


class A2AUnsupportedBindingError(A2AClientError):
    """Raised when local and remote transport configuration has no overlap."""

    error_code = "unsupported_binding"


class A2AUnsupportedOperationError(A2AClientError):
    """Raised when peer does not support an attempted operation."""

    error_code = "unsupported_operation"


class A2APeerProtocolError(A2AClientError):
    """Raised when peer response violates JSON-RPC / task contract."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "peer_protocol_error",
        rpc_code: int | None = None,
        http_status: int | None = None,
        data: object | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.code = rpc_code
        self.http_status = http_status
        self.data = data


__all__ = [
    "A2AClientError",
    "A2AAgentUnavailableError",
    "A2AAuthenticationError",
    "A2APermissionDeniedError",
    "A2AClientResetRequiredError",
    "A2ATimeoutError",
    "A2AUnsupportedBindingError",
    "A2AUnsupportedOperationError",
    "A2APeerProtocolError",
]

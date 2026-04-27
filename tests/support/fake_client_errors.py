from __future__ import annotations

from a2a.client.errors import A2AClientError


class FakeA2AClientHTTPError(A2AClientError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP Error {status_code}: {message}")


class FakeA2AClientJSONError(A2AClientError):
    pass


class FakeA2AClientJSONRPCError(A2AClientError):
    def __init__(self, response: object) -> None:
        self.response = response
        error = getattr(response, "error", None)
        message = getattr(error, "message", "JSON-RPC error")
        super().__init__(message)

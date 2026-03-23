from __future__ import annotations

from typing import Any

from a2a.types import A2AError, InvalidParamsError, JSONRPCError


def invalid_params_error(
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> A2AError:
    return A2AError(root=InvalidParamsError(message=message, data=data))


def method_not_supported_error(
    *,
    method: str,
    supported_methods: list[str],
    protocol_version: str,
) -> JSONRPCError:
    return JSONRPCError(
        code=-32601,
        message=f"Unsupported method: {method}",
        data={
            "type": "METHOD_NOT_SUPPORTED",
            "method": method,
            "supported_methods": supported_methods,
            "protocol_version": protocol_version,
        },
    )


def session_forbidden_error(code: int, *, session_id: str) -> JSONRPCError:
    return JSONRPCError(
        code=code,
        message="Session forbidden",
        data={"type": "SESSION_FORBIDDEN", "session_id": session_id},
    )


def session_not_found_error(code: int, *, session_id: str) -> JSONRPCError:
    return JSONRPCError(
        code=code,
        message="Session not found",
        data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
    )


def interrupt_not_found_error(
    code: int,
    *,
    request_id: str,
    expired: bool = False,
) -> JSONRPCError:
    return JSONRPCError(
        code=code,
        message="Interrupt request expired" if expired else "Interrupt request not found",
        data={
            "type": "INTERRUPT_REQUEST_EXPIRED" if expired else "INTERRUPT_REQUEST_NOT_FOUND",
            "request_id": request_id,
        },
    )


def upstream_http_error(
    code: int,
    *,
    upstream_status: int,
    method: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    detail: str | None = None,
) -> JSONRPCError:
    data: dict[str, Any] = {
        "type": "UPSTREAM_HTTP_ERROR",
        "upstream_status": upstream_status,
    }
    if method is not None:
        data["method"] = method
    if session_id is not None:
        data["session_id"] = session_id
    if request_id is not None:
        data["request_id"] = request_id
    if detail is not None:
        data["detail"] = detail
    return JSONRPCError(code=code, message="Upstream OpenCode error", data=data)


def upstream_unreachable_error(
    code: int,
    *,
    method: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> JSONRPCError:
    data: dict[str, Any] = {"type": "UPSTREAM_UNREACHABLE"}
    if method is not None:
        data["method"] = method
    if session_id is not None:
        data["session_id"] = session_id
    if request_id is not None:
        data["request_id"] = request_id
    return JSONRPCError(code=code, message="Upstream OpenCode unreachable", data=data)


def upstream_payload_error(
    code: int,
    *,
    detail: str,
    method: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> JSONRPCError:
    data: dict[str, Any] = {
        "type": "UPSTREAM_PAYLOAD_ERROR",
        "detail": detail,
    }
    if method is not None:
        data["method"] = method
    if session_id is not None:
        data["session_id"] = session_id
    if request_id is not None:
        data["request_id"] = request_id
    return JSONRPCError(code=code, message="Upstream OpenCode payload mismatch", data=data)


__all__ = [
    "interrupt_not_found_error",
    "invalid_params_error",
    "method_not_supported_error",
    "session_forbidden_error",
    "session_not_found_error",
    "upstream_http_error",
    "upstream_payload_error",
    "upstream_unreachable_error",
]

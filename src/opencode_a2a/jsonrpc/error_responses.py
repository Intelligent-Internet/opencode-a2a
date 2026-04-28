from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from a2a.utils.errors import (
    JSON_RPC_ERROR_CODE_MAP,
    A2AError,
    InvalidParamsError,
)

from ..protocol_versions import A2A_PROTOCOL_VERSION
from .models import JSONRPCError

A2A_ERROR_DOMAIN = "a2a-protocol.org"
GOOGLE_RPC_ERROR_INFO_TYPE = "type.googleapis.com/google.rpc.ErrorInfo"
STANDARD_JSONRPC_ERROR_MESSAGES = {
    -32700: "Invalid JSON payload",
    -32600: "Request payload validation error",
    -32601: "Method not found",
    -32602: "Invalid parameters",
    -32603: "Internal error",
}
STANDARD_JSONRPC_ERROR_CODES = frozenset(STANDARD_JSONRPC_ERROR_MESSAGES)


def _to_upper_snake_case(name: str) -> str:
    normalized: list[str] = []
    previous_was_lower = False
    for char in name:
        if char.isupper() and previous_was_lower:
            normalized.append("_")
        if char in {" ", "-"}:
            normalized.append("_")
            previous_was_lower = False
            continue
        normalized.append(char.upper())
        previous_was_lower = char.islower()
    return "".join(normalized).strip("_")


def _to_lower_camel_case(name: str) -> str:
    if "_" not in name:
        return name
    head, *tail = [part for part in name.split("_") if part]
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _camelize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {_to_lower_camel_case(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _stringify_metadata_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _build_error_info_detail(
    *,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "@type": GOOGLE_RPC_ERROR_INFO_TYPE,
        "reason": _to_upper_snake_case(reason),
        "domain": A2A_ERROR_DOMAIN,
    }
    if metadata:
        payload["metadata"] = {
            _to_lower_camel_case(str(key)): _stringify_metadata_value(value)
            for key, value in metadata.items()
            if value is not None
        }
    return payload


def _build_context_detail(type_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "@type": f"type.googleapis.com/opencode_a2a.{type_name}",
        **_camelize(dict(payload)),
    }


def _reason_from_error(error: object) -> str | None:
    data = getattr(error, "data", None)
    if isinstance(data, Mapping):
        data_type = data.get("type")
        if isinstance(data_type, str) and data_type.strip():
            return data_type
    class_name = type(error).__name__
    if class_name.endswith("Error") and class_name != "JSONRPCError":
        return class_name[:-5]
    return None


def _metadata_from_error(error: object) -> dict[str, Any]:
    data = getattr(error, "data", None)
    if not isinstance(data, Mapping):
        return {}
    return {str(key): value for key, value in data.items() if key != "type"}


def adapt_jsonrpc_error(error: JSONRPCError | A2AError) -> JSONRPCError | A2AError:
    reason_source: object = error
    if isinstance(error, A2AError):
        root_error = JSONRPCError(
            code=JSON_RPC_ERROR_CODE_MAP.get(type(error), -32603),
            message=error.message,
            data=error.data,
        )
    else:
        root_error = error
        reason_source = root_error
    root_data = root_error.data

    if root_error.code in STANDARD_JSONRPC_ERROR_CODES:
        adapted_data = None
        if isinstance(root_data, Mapping):
            adapted_data = _camelize(
                {str(key): value for key, value in root_data.items() if key != "type"}
            )
        elif root_data is not None:
            adapted_data = root_data
        return JSONRPCError(
            code=root_error.code,
            message=STANDARD_JSONRPC_ERROR_MESSAGES[root_error.code],
            data=adapted_data,
        )

    reason = _reason_from_error(reason_source)
    metadata = _metadata_from_error(reason_source)
    details: list[dict[str, Any]] = []
    if reason is not None:
        details.append(_build_error_info_detail(reason=reason, metadata=metadata))
    if metadata:
        details.append(_build_context_detail("ErrorContext", metadata))

    message = root_error.message
    if message is None:
        message = STANDARD_JSONRPC_ERROR_MESSAGES.get(root_error.code, "Internal error")

    return JSONRPCError(
        code=root_error.code,
        message=message,
        data=details or None,
    )


def build_http_error_body(
    *,
    status_code: int,
    status: str,
    message: str,
    reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    if reason is not None:
        details.append(_build_error_info_detail(reason=reason, metadata=metadata))
    if metadata:
        details.append(_build_context_detail("HttpErrorContext", dict(metadata)))

    error_payload: dict[str, Any] = {
        "code": status_code,
        "status": status,
        "message": message,
    }
    if details:
        error_payload["details"] = details
    return {"error": error_payload}


def invalid_params_error(
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> A2AError:
    return InvalidParamsError(message=message, data=data)


def method_not_supported_error(
    *,
    method: str,
    supported_methods: list[str],
) -> JSONRPCError:
    return JSONRPCError(
        code=-32601,
        message=f"Unsupported method: {method}",
        data={
            "type": "METHOD_NOT_SUPPORTED",
            "method": method,
            "supported_methods": supported_methods,
            "protocol_version": A2A_PROTOCOL_VERSION,
        },
    )


def version_not_supported_error(
    *,
    requested_version: str,
    supported_protocol_versions: list[str],
    default_protocol_version: str,
) -> JSONRPCError:
    return JSONRPCError(
        code=-32001,
        message=f"Unsupported A2A version: {requested_version}",
        data={
            "type": "VERSION_NOT_SUPPORTED",
            "requested_version": requested_version,
            "supported_protocol_versions": supported_protocol_versions,
            "default_protocol_version": default_protocol_version,
        },
    )


def session_forbidden_error(code: int, *, session_id: str) -> JSONRPCError:
    return JSONRPCError(
        code=code,
        message="Session forbidden",
        data={"type": "SESSION_FORBIDDEN", "session_id": session_id},
    )


def authorization_forbidden_error(
    code: int,
    *,
    method: str,
    capability: str,
    credential_id: str | None = None,
) -> JSONRPCError:
    data: dict[str, Any] = {
        "type": "AUTHORIZATION_FORBIDDEN",
        "method": method,
        "capability": capability,
    }
    if credential_id is not None:
        data["credential_id"] = credential_id
    return JSONRPCError(
        code=code,
        message="Authorization forbidden",
        data=data,
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


def interrupt_type_mismatch_error(
    code: int,
    *,
    request_id: str,
    expected_interrupt_type: str,
    actual_interrupt_type: str,
) -> JSONRPCError:
    return JSONRPCError(
        code=code,
        message="Interrupt callback type mismatch",
        data={
            "type": "INTERRUPT_TYPE_MISMATCH",
            "request_id": request_id,
            "expected_interrupt_type": expected_interrupt_type,
            "actual_interrupt_type": actual_interrupt_type,
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
    detail: str | None = None,
) -> JSONRPCError:
    data: dict[str, Any] = {"type": "UPSTREAM_UNREACHABLE"}
    if method is not None:
        data["method"] = method
    if session_id is not None:
        data["session_id"] = session_id
    if request_id is not None:
        data["request_id"] = request_id
    if detail is not None:
        data["detail"] = detail
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

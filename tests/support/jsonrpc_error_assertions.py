from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opencode_a2a.jsonrpc.error_responses import GOOGLE_RPC_ERROR_INFO_TYPE


def _camelize_key(name: str) -> str:
    if "_" not in name:
        return name
    head, *tail = [part for part in name.split("_") if part]
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _stringify_metadata_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    raise TypeError(f"Unsupported metadata value: {value!r}")


def error_info_detail(error_payload: Mapping[str, Any]) -> dict[str, Any]:
    data = error_payload.get("data")
    if not isinstance(data, list):
        raise TypeError(f"Expected list-backed error data, got {type(data)!r}")
    for item in data:
        if isinstance(item, dict) and item.get("@type") == GOOGLE_RPC_ERROR_INFO_TYPE:
            return item
    raise AssertionError("google.rpc.ErrorInfo detail not found")


def error_context_detail(error_payload: Mapping[str, Any]) -> dict[str, Any] | None:
    data = error_payload.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and item.get("@type", "").startswith(
            "type.googleapis.com/opencode_a2a."
        ):
            return item
    return None


def assert_v1_error_reason(
    error_payload: Mapping[str, Any],
    *,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    detail = error_info_detail(error_payload)
    assert detail["reason"] == reason
    assert detail["domain"] == "a2a-protocol.org"
    if metadata is not None:
        assert detail["metadata"] == {
            _camelize_key(str(key)): _stringify_metadata_value(value)
            for key, value in metadata.items()
        }


def assert_v1_error_metadata_contains(
    error_payload: Mapping[str, Any],
    *,
    reason: str,
    metadata: Mapping[str, Any],
) -> None:
    detail = error_info_detail(error_payload)
    assert detail["reason"] == reason
    actual = detail.get("metadata", {})
    for key, value in metadata.items():
        assert actual[_camelize_key(str(key))] == _stringify_metadata_value(value)


def assert_v1_error_context(
    error_payload: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> None:
    detail = error_context_detail(error_payload)
    assert detail is not None
    detail = dict(detail)
    detail.pop("@type", None)
    assert detail == {_camelize_key(str(key)): value for key, value in metadata.items()}

"""Helpers for outbound request metadata and call-context construction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a.client.client import ClientCallContext

from ..extension_negotiation import merge_extension_service_parameters
from ..protocol_versions import normalize_protocol_version
from ..trace_context import current_trace_headers
from .auth import encode_basic_auth


def build_default_headers(
    bearer_token: str | None,
    basic_auth: str | None = None,
    protocol_version: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif basic_auth:
        headers["Authorization"] = f"Basic {encode_basic_auth(basic_auth)}"
    if protocol_version:
        headers["A2A-Version"] = normalize_protocol_version(protocol_version)
    return headers


def split_request_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None, tuple[str, ...] | None]:
    request_metadata: dict[str, Any] = {}
    extra_headers: dict[str, str] = {}
    requested_extensions: list[str] = []
    for key, value in dict(metadata or {}).items():
        if isinstance(key, str) and key.lower() == "authorization":
            if value is not None:
                extra_headers["Authorization"] = str(value)
            continue
        if isinstance(key, str) and key.lower() == "a2a-version":
            if value is not None:
                extra_headers["A2A-Version"] = normalize_protocol_version(str(value))
            continue
        if isinstance(key, str) and key.lower() == "traceparent":
            if value is not None:
                extra_headers["traceparent"] = str(value)
            continue
        if isinstance(key, str) and key.lower() == "tracestate":
            if value is not None:
                extra_headers["tracestate"] = str(value)
            continue
        if isinstance(key, str) and key.lower() == "a2a-extensions":
            if isinstance(value, str):
                requested_extensions.extend(
                    item.strip() for item in value.split(",") if item.strip()
                )
            elif isinstance(value, list | tuple | set):
                requested_extensions.extend(
                    str(item).strip() for item in value if isinstance(item, str) and item.strip()
                )
            continue
        request_metadata[key] = value
    return (
        request_metadata or None,
        extra_headers or None,
        tuple(requested_extensions) or None,
    )


def build_call_context(
    bearer_token: str | None,
    extra_headers: Mapping[str, str] | None,
    extensions: tuple[str, ...] | None = None,
    basic_auth: str | None = None,
    protocol_version: str | None = None,
) -> ClientCallContext | None:
    merged_headers = build_default_headers(bearer_token, basic_auth, protocol_version)
    merged_headers.update(current_trace_headers())
    if extra_headers:
        merged_headers.update(extra_headers)
    service_parameters = merge_extension_service_parameters(None, extensions)
    if not merged_headers and not service_parameters:
        return None
    return ClientCallContext(
        state={
            "headers": dict(merged_headers),
            "http_kwargs": {"headers": dict(merged_headers)},
        },
        service_parameters=service_parameters,
    )


__all__ = [
    "build_call_context",
    "build_default_headers",
    "split_request_metadata",
]

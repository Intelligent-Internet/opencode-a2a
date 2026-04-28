"""Helpers for outbound request metadata and call-context construction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a.client.client import ClientCallContext

from ..extension_negotiation import merge_extension_service_parameters
from ..protocol_versions import A2A_PROTOCOL_VERSION
from ..trace_context import current_trace_headers
from .auth import encode_basic_auth


def build_default_headers(
    bearer_token: str | None,
    basic_auth: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {"A2A-Version": A2A_PROTOCOL_VERSION}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif basic_auth:
        headers["Authorization"] = f"Basic {encode_basic_auth(basic_auth)}"
    return headers


def split_request_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, str] | None, tuple[str, ...] | None]:
    request_metadata: dict[str, Any] = {}
    extra_headers: dict[str, str] = {}
    requested_extensions: list[str] = []
    for key, value in (metadata or {}).items():
        normalized_key = key.lower()
        if normalized_key == "authorization":
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("Authorization metadata header must be a string.")
                extra_headers["Authorization"] = value
            continue
        if normalized_key == "a2a-version":
            raise ValueError("A2A-Version is fixed to 1.0 and must not be overridden.")
        if normalized_key == "traceparent":
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("traceparent metadata header must be a string.")
                extra_headers["traceparent"] = value
            continue
        if normalized_key == "tracestate":
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("tracestate metadata header must be a string.")
                extra_headers["tracestate"] = value
            continue
        if normalized_key == "a2a-extensions":
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError("A2A-Extensions metadata header must be a string.")
            requested_extensions.extend(item.strip() for item in value.split(",") if item.strip())
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
) -> ClientCallContext:
    merged_headers = build_default_headers(bearer_token, basic_auth)
    merged_headers.update(current_trace_headers())
    if extra_headers:
        merged_headers.update(extra_headers)
    service_parameters = merge_extension_service_parameters(None, extensions)
    return ClientCallContext(
        state={
            "headers": dict(merged_headers),
            "http_kwargs": {"headers": dict(merged_headers)},
        },
        service_parameters=service_parameters,
    )

from __future__ import annotations

from base64 import b64encode

import pytest

from opencode_a2a.client.request_context import (
    ClientCallContext,
    build_call_context,
    build_default_headers,
    split_request_metadata,
)
from opencode_a2a.trace_context import TraceContext, bind_trace_context


def test_split_request_metadata_and_default_headers() -> None:
    request_metadata, extra_headers, requested_extensions = split_request_metadata(
        {
            "authorization": "Bearer explicit-token",
            "A2A-Version": "1.0.0",
            "A2A-Extensions": "https://example.com/ext-b, https://example.com/ext-a",
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "tracestate": "vendor=value",
            "trace_id": "trace-1",
        }
    )

    assert request_metadata == {"trace_id": "trace-1"}
    assert extra_headers == {
        "Authorization": "Bearer explicit-token",
        "A2A-Version": "1.0",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "vendor=value",
    }
    assert requested_extensions == (
        "https://example.com/ext-b",
        "https://example.com/ext-a",
    )
    assert build_default_headers("peer-token") == {"Authorization": "Bearer peer-token"}


def test_build_default_headers_encodes_basic_auth_credentials() -> None:
    encoded = b64encode(b"user:pass").decode()

    assert build_default_headers(None, "user:pass") == {"Authorization": f"Basic {encoded}"}


def test_build_default_headers_accepts_pre_encoded_basic_auth() -> None:
    encoded = b64encode(b"user:pass").decode()

    assert build_default_headers(None, encoded) == {"Authorization": f"Basic {encoded}"}


def test_build_default_headers_rejects_invalid_basic_auth() -> None:
    with pytest.raises(ValueError, match="username:password"):
        build_default_headers(None, "not-basic-auth")


def test_build_default_headers_prefers_bearer_over_basic_auth() -> None:
    assert build_default_headers("peer-token", "user:pass") == {
        "Authorization": "Bearer peer-token"
    }


def test_build_default_headers_includes_protocol_version() -> None:
    assert build_default_headers("peer-token", protocol_version="1.0.0") == {
        "Authorization": "Bearer peer-token",
        "A2A-Version": "1.0",
    }


def test_build_call_context_without_headers_returns_none() -> None:
    assert build_call_context(None, None) is None


def test_build_call_context_includes_current_trace_headers() -> None:
    with bind_trace_context(
        TraceContext(
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            tracestate="vendor=value",
        )
    ):
        context = build_call_context(None, None)

    assert context is not None
    assert context.state["headers"] == {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "vendor=value",
    }


def test_build_call_context_preserves_explicit_trace_headers_over_current_context() -> None:
    with bind_trace_context(
        TraceContext(
            traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            tracestate="upstream=1",
        )
    ):
        context = build_call_context(
            "peer-token",
            {
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                "tracestate": "vendor=value",
            },
        )

    assert context is not None
    assert context.state["headers"] == {
        "Authorization": "Bearer peer-token",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "vendor=value",
    }


def test_build_call_context_carries_default_headers_without_interceptor_layer() -> None:
    context = build_call_context("peer-token", {"X-Trace-Id": "trace-1"})

    assert isinstance(context, ClientCallContext)
    assert context.state["headers"] == {
        "Authorization": "Bearer peer-token",
        "X-Trace-Id": "trace-1",
    }
    assert context.state["http_kwargs"]["headers"] == {
        "Authorization": "Bearer peer-token",
        "X-Trace-Id": "trace-1",
    }


def test_build_call_context_merges_extension_service_parameters() -> None:
    context = build_call_context(
        "peer-token",
        {"X-Trace-Id": "trace-1"},
        ("https://example.com/ext-b", "https://example.com/ext-a"),
    )

    assert isinstance(context, ClientCallContext)
    assert context.service_parameters == {
        "A2A-Extensions": "https://example.com/ext-a,https://example.com/ext-b"
    }

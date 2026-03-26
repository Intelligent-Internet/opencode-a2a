from __future__ import annotations

from base64 import b64encode

import pytest

from opencode_a2a.client.request_context import (
    ClientCallContext,
    HeaderInterceptor,
    build_call_context,
    build_client_interceptors,
    build_default_headers,
    split_request_metadata,
)


def test_split_request_metadata_and_default_headers() -> None:
    request_metadata, extra_headers = split_request_metadata(
        {"authorization": "Bearer explicit-token", "trace_id": "trace-1"}
    )

    assert request_metadata == {"trace_id": "trace-1"}
    assert extra_headers == {"Authorization": "Bearer explicit-token"}
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


def test_build_call_context_without_headers_returns_none() -> None:
    assert build_call_context(None, None) is None


def test_build_client_interceptors_uses_header_interceptor() -> None:
    interceptors = build_client_interceptors("peer-token")

    assert len(interceptors) == 1
    assert isinstance(interceptors[0], HeaderInterceptor)


@pytest.mark.asyncio
async def test_header_interceptor_merges_static_and_dynamic_headers() -> None:
    interceptor = HeaderInterceptor({"Authorization": "Bearer peer-token"})
    context = ClientCallContext(state={"headers": {"X-Trace-Id": "trace-1"}})

    request_payload, http_kwargs = await interceptor.intercept(
        "message/send",
        {"jsonrpc": "2.0"},
        {"headers": {"Accept": "application/json"}},
        agent_card=None,
        context=context,
    )

    assert request_payload == {"jsonrpc": "2.0"}
    assert http_kwargs["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer peer-token",
        "X-Trace-Id": "trace-1",
    }

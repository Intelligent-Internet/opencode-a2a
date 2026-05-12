from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

from a2a.auth.user import UnauthenticatedUser
from a2a.server.context import ServerCallContext

from opencode_a2a.server.context_helpers import (
    AuthenticatedIdentityUser,
    normalize_server_call_context,
)


def test_normalize_server_call_context_preserves_existing_normalized_context() -> None:
    context = ServerCallContext(
        state={"identity": "opaque:test-user"},
        tenant="tenant-1",
        requested_extensions={"ext-a"},
        user=AuthenticatedIdentityUser("opaque:test-user"),
    )

    normalized = normalize_server_call_context(context)

    assert normalized is context
    assert normalized.user.is_authenticated is True
    assert normalized.user.user_name == "opaque:test-user"


def test_normalize_server_call_context_coerces_mapping_and_promotes_identity_user() -> None:
    context = SimpleNamespace(
        state=MappingProxyType({"identity": "opaque:test-user", "trace_id": "abc123"}),
        tenant=123,
        requested_extensions=["ext-a", 7, "ext-a"],
        user=UnauthenticatedUser(),
    )

    normalized = normalize_server_call_context(context)

    assert normalized is not context
    assert normalized.state == {"identity": "opaque:test-user", "trace_id": "abc123"}
    assert isinstance(normalized.state, dict)
    assert normalized.tenant == ""
    assert normalized.requested_extensions == {"ext-a", "7"}
    assert normalized.user.is_authenticated is True
    assert normalized.user.user_name == "opaque:test-user"


def test_normalize_server_call_context_falls_back_for_invalid_shapes() -> None:
    context = SimpleNamespace(
        state=object(),
        tenant="tenant-3",
        requested_extensions=object(),
        user="not-a-user",
    )

    normalized = normalize_server_call_context(context)

    assert normalized.state == {}
    assert normalized.tenant == "tenant-3"
    assert normalized.requested_extensions == set()
    assert isinstance(normalized.user, UnauthenticatedUser)

from __future__ import annotations

import httpx
import pytest

from opencode_a2a.opencode_upstream_client import UpstreamContractError
from opencode_a2a.upstream.v2 import V2UpstreamAdapter
from opencode_a2a.upstream.v2_profile import (
    exclusive_permissions,
    validate_exclusive_configuration,
    validate_exclusive_settings,
)
from tests.support.settings import make_settings


def test_exclusive_profile_requires_private_separate_authenticated_upstream() -> None:
    settings = make_settings()
    with pytest.raises(ValueError, match="credentials"):
        validate_exclusive_settings(settings)
    settings.opencode_auth_password = "test-only-upstream"  # pragma: allowlist secret
    settings.opencode_workspace_root = "/workspace"
    validate_exclusive_settings(settings)
    settings.opencode_base_url = "http://example.test:4096"
    with pytest.raises(ValueError, match="loopback"):
        validate_exclusive_settings(settings)


def test_background_and_custom_producers_are_rejected() -> None:
    plugins = {"data": [{"source": {"type": "builtin"}}]}
    validate_exclusive_configuration([{"type": "document", "info": {"warming": False}}], plugins)
    for info in (
        {"warming": True},
        {"plugins": ["untrusted"]},
        {"mcp": {"servers": {"remote": {}}}},
    ):
        with pytest.raises(UpstreamContractError):
            validate_exclusive_configuration([{"type": "document", "info": info}], plugins)
    with pytest.raises(UpstreamContractError, match="builtin"):
        validate_exclusive_configuration([], {"data": [{"source": {"type": "local"}}]})
    permissions = exclusive_permissions()
    assert permissions == [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*", "effect": "ask"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
    ]
    assert permissions[0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert permissions[-1] == {"action": "external_directory", "resource": "*", "effect": "deny"}


@pytest.mark.asyncio
async def test_exclusive_workspace_is_fixed_without_changing_default_v2_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exclusive = V2UpstreamAdapter(
        make_settings(
            opencode_api_version="v2",
            opencode_v2_execution="exclusive",
            opencode_auth_password="test-only-upstream",  # pragma: allowlist secret
            opencode_workspace_root="/workspace",
        )
    )
    transport = V2UpstreamAdapter(
        make_settings(opencode_api_version="v2", opencode_workspace_root="/workspace")
    )
    requests: list[httpx.Request] = []

    def unexpected(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("A location override must reject before upstream HTTP")

    monkeypatch.setattr(exclusive._client, "_transport", httpx.MockTransport(unexpected))
    try:
        assert exclusive._resolve_location(None, None) == "/workspace"
        with pytest.raises(ValueError, match="fixed configured workspace"):
            await exclusive.create_session(directory="/workspace/other")
        assert transport._resolve_location("/workspace/other", None) == "/workspace/other"
        assert exclusive._v2_execution.sessions == {}
        assert requests == []
    finally:
        await exclusive.close()
        await transport.close()

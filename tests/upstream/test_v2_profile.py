from __future__ import annotations

import pytest

from opencode_a2a.opencode_upstream_client import UpstreamContractError
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

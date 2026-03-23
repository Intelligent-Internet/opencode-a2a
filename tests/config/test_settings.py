import os
from unittest import mock

import pytest
from pydantic import ValidationError

from opencode_a2a import __version__
from opencode_a2a.config import Settings


def test_settings_missing_required():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        # Should mention missing required fields
        errors = excinfo.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "A2A_BEARER_TOKEN" in field_names


def test_settings_valid():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_TIMEOUT": "300",
        "OPENCODE_WORKSPACE_ROOT": "/srv/workspaces/alpha",
        "A2A_MAX_REQUEST_BODY_BYTES": "2048",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "7200",
        "A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS": "120",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "0.75",
        "A2A_ENABLE_SESSION_SHELL": "true",
        "A2A_SANDBOX_MODE": "danger-full-access",
        "A2A_SANDBOX_FILESYSTEM_SCOPE": "unrestricted",
        "A2A_SANDBOX_WRITABLE_ROOTS": "/srv/workspaces/alpha,/tmp/opencode",
        "A2A_NETWORK_ACCESS": "restricted",
        "A2A_NETWORK_ALLOWED_DOMAINS": '["api.openai.com", "github.com"]',
        "A2A_APPROVAL_POLICY": "never",
        "A2A_APPROVAL_ESCALATION_BEHAVIOR": "unsupported",
        "A2A_WRITE_ACCESS_SCOPE": "unrestricted",
        "A2A_WRITE_ACCESS_OUTSIDE_WORKSPACE": "allowed",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()
        assert settings.a2a_bearer_token == "test-token"
        assert settings.opencode_timeout == 300.0
        assert settings.opencode_workspace_root == "/srv/workspaces/alpha"
        assert settings.a2a_max_request_body_bytes == 2048
        assert settings.a2a_interrupt_request_ttl_seconds == 7200.0
        assert settings.a2a_interrupt_request_tombstone_ttl_seconds == 120.0
        assert settings.a2a_cancel_abort_timeout_seconds == 0.75
        assert settings.a2a_enable_session_shell is True
        assert settings.a2a_sandbox_mode == "danger-full-access"
        assert settings.a2a_sandbox_filesystem_scope == "unrestricted"
        assert settings.a2a_sandbox_writable_roots == ("/srv/workspaces/alpha", "/tmp/opencode")
        assert settings.a2a_network_access == "restricted"
        assert settings.a2a_network_allowed_domains == ("api.openai.com", "github.com")
        assert settings.a2a_approval_policy == "never"
        assert settings.a2a_approval_escalation_behavior == "unsupported"
        assert settings.a2a_write_access_scope == "unrestricted"
        assert settings.a2a_write_access_outside_workspace == "allowed"
        assert settings.a2a_version == __version__


def test_settings_ignore_legacy_opencode_directory_env() -> None:
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_DIRECTORY": "/legacy/workspace",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()

    assert settings.opencode_workspace_root is None


def test_settings_reject_negative_max_request_body_bytes():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "A2A_MAX_REQUEST_BODY_BYTES": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    field_names = [e["loc"][0] for e in excinfo.value.errors()]
    assert "A2A_MAX_REQUEST_BODY_BYTES" in field_names


def test_settings_reject_declared_writable_roots_outside_workspace_for_workspace_only_scope():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_WORKSPACE_ROOT": "/srv/workspaces/alpha",
        "A2A_SANDBOX_WRITABLE_ROOTS": "/srv/workspaces/alpha,/tmp/opencode",
        "A2A_WRITE_ACCESS_SCOPE": "workspace_only",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "Declared writable roots must stay within the workspace root" in str(excinfo.value)


def test_settings_reject_declared_writable_roots_when_write_scope_is_none():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_WORKSPACE_ROOT": "/srv/workspaces/alpha",
        "A2A_SANDBOX_WRITABLE_ROOTS": "/srv/workspaces/alpha/tmp",
        "A2A_WRITE_ACCESS_SCOPE": "none",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "Declared writable roots are incompatible with A2A_WRITE_ACCESS_SCOPE=none" in str(
        excinfo.value
    )

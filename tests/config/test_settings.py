import json
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
        assert "Configure runtime authentication via A2A_STATIC_AUTH_CREDENTIALS" in str(
            excinfo.value
        )


def test_settings_valid():
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                },
                {
                    "scheme": "basic",
                    "username": "operator",
                    "password": "op-pass",  # pragma: allowlist secret
                },
            ]
        ),
        "OPENCODE_TIMEOUT": "300",
        "OPENCODE_WORKSPACE_ROOT": "/srv/workspaces/alpha",
        "A2A_HTTP_GZIP_MINIMUM_SIZE": "2048",
        "A2A_MAX_REQUEST_BODY_BYTES": "2048",
        "A2A_PENDING_SESSION_CLAIM_TTL_SECONDS": "45",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "7200",
        "A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS": "120",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "0.75",
        "A2A_ENABLE_SESSION_SHELL": "true",
        "OPENCODE_MAX_CONCURRENT_REQUESTS": "12",
        "OPENCODE_MAX_CONCURRENT_STREAMS": "3",
        "A2A_CLIENT_BASIC_AUTH": "user:pass",
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
        assert len(settings.a2a_static_auth_credentials) == 2
        assert settings.a2a_static_auth_credentials[0].principal == "automation"
        assert settings.a2a_static_auth_credentials[1].principal == "operator"
        assert settings.opencode_timeout == 300.0
        assert settings.opencode_workspace_root == "/srv/workspaces/alpha"
        assert settings.a2a_http_gzip_minimum_size == 2048
        assert settings.a2a_max_request_body_bytes == 2048
        assert settings.a2a_pending_session_claim_ttl_seconds == 45.0
        assert settings.a2a_interrupt_request_ttl_seconds == 7200.0
        assert settings.a2a_interrupt_request_tombstone_ttl_seconds == 120.0
        assert settings.a2a_cancel_abort_timeout_seconds == 0.75
        assert settings.opencode_max_concurrent_requests == 12
        assert settings.opencode_max_concurrent_streams == 3
        assert settings.a2a_enable_session_shell is True
        assert settings.a2a_client_basic_auth == "user:pass"
        assert settings.a2a_sandbox_mode == "danger-full-access"
        assert settings.a2a_sandbox_filesystem_scope == "unrestricted"
        assert settings.a2a_sandbox_writable_roots == ("/srv/workspaces/alpha", "/tmp/opencode")
        assert settings.a2a_network_access == "restricted"
        assert settings.a2a_network_allowed_domains == ("api.openai.com", "github.com")
        assert settings.a2a_approval_policy == "never"
        assert settings.a2a_approval_escalation_behavior == "unsupported"
        assert settings.a2a_write_access_scope == "unrestricted"
        assert settings.a2a_write_access_outside_workspace == "allowed"
        assert settings.a2a_task_store_backend == "database"
        assert settings.a2a_task_store_database_url == "sqlite+aiosqlite:///./opencode-a2a.db"
        assert settings.a2a_version == __version__
        assert settings.a2a_protocol_version == "1.0"
        assert settings.a2a_supported_protocol_versions == ("1.0",)


def test_settings_normalize_protocol_versions() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
        "A2A_PROTOCOL_VERSION": "1.0.0",
        "A2A_SUPPORTED_PROTOCOL_VERSIONS": "1.0.0,1.0",
        "A2A_CLIENT_PROTOCOL_VERSION": "1.0.0",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()

    assert settings.a2a_protocol_version == "1.0"
    assert settings.a2a_supported_protocol_versions == ("1.0",)
    assert settings.a2a_client_protocol_version == "1.0"


def test_settings_reject_legacy_protocol_configuration() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
        "A2A_PROTOCOL_VERSION": "0.3.0",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "Only A2A v1.0 is supported" in str(excinfo.value)


def test_settings_allow_explicit_memory_backend() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
        "A2A_TASK_STORE_BACKEND": "memory",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()

    assert settings.a2a_task_store_backend == "memory"


def test_settings_reject_legacy_runtime_auth_envs() -> None:
    env = {
        "A2A_BEARER_TOKEN": "test-token",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "Configure runtime authentication via A2A_STATIC_AUTH_CREDENTIALS" in str(excinfo.value)


def test_settings_accept_static_auth_registry() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "credential_id": "bot-alpha",
                    "scheme": "bearer",
                    "token": "token-alpha",
                    "principal": "automation-alpha",
                },
                {
                    "scheme": "basic",
                    "username": "operator",
                    "password": "op-pass",  # pragma: allowlist secret
                    "capabilities": ["session_shell"],
                },
                {
                    "scheme": "bearer",
                    "token": "token-disabled",
                    "principal": "disabled",
                    "enabled": False,
                },
            ]
        )
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()

    assert len(settings.a2a_static_auth_credentials) == 3
    assert settings.a2a_static_auth_credentials[0].credential_id == "bot-alpha"
    assert settings.a2a_static_auth_credentials[0].principal == "automation-alpha"
    assert settings.a2a_static_auth_credentials[1].principal == "operator"
    assert settings.a2a_static_auth_credentials[1].capabilities == ("session_shell",)
    assert settings.a2a_static_auth_credentials[2].enabled is False


def test_settings_reject_registry_without_enabled_credentials() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "token-disabled",
                    "principal": "disabled",
                    "enabled": False,
                }
            ]
        )
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "A2A_STATIC_AUTH_CREDENTIALS must contain at least one enabled credential" in str(
        excinfo.value
    )


def test_settings_reject_basic_registry_principal_override() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "basic",
                    "username": "operator",
                    "password": "op-pass",  # pragma: allowlist secret
                    "principal": "custom-operator",
                }
            ]
        )
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "Static basic credential does not accept principal" in str(excinfo.value)


def test_settings_reject_registry_bearer_without_explicit_principal() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "token-alpha",
                }
            ]
        )
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    assert "Static bearer credential requires explicit principal" in str(excinfo.value)


def test_settings_ignore_legacy_opencode_directory_env() -> None:
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
        "OPENCODE_DIRECTORY": "/legacy/workspace",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()

    assert settings.opencode_workspace_root is None


def test_settings_reject_negative_max_request_body_bytes():
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
        "A2A_MAX_REQUEST_BODY_BYTES": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    field_names = [e["loc"][0] for e in excinfo.value.errors()]
    assert "A2A_MAX_REQUEST_BODY_BYTES" in field_names


def test_settings_reject_negative_http_gzip_minimum_size():
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
        "A2A_HTTP_GZIP_MINIMUM_SIZE": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    field_names = [e["loc"][0] for e in excinfo.value.errors()]
    assert "A2A_HTTP_GZIP_MINIMUM_SIZE" in field_names


def test_settings_reject_declared_writable_roots_outside_workspace_for_workspace_only_scope():
    env = {
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
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
        "A2A_STATIC_AUTH_CREDENTIALS": json.dumps(
            [
                {
                    "scheme": "bearer",
                    "token": "test-token",
                    "principal": "automation",
                }
            ]
        ),
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

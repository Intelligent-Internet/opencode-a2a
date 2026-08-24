import json
import os
from unittest import mock

import pytest
from pydantic import ValidationError

from opencode_a2a import __version__
from opencode_a2a.config import Settings
from opencode_a2a.protocol_versions import (
    A2A_PROTOCOL_VERSION,
    A2A_SUPPORTED_PROTOCOL_VERSIONS,
)
from tests.support.settings import make_settings


def test_settings_missing_required():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        assert "Configure runtime authentication via A2A_STATIC_AUTH_CREDENTIALS" in str(
            excinfo.value
        )


def test_settings_use_bounded_upstream_defaults() -> None:
    settings = make_settings()

    assert settings.opencode_timeout_stream == 900.0
    assert settings.opencode_max_concurrent_requests == 32
    assert settings.opencode_max_concurrent_streams == 8


def test_settings_use_bounded_admission_defaults() -> None:
    settings = make_settings()

    assert settings.a2a_rate_limit_enabled is True
    assert settings.a2a_metrics_enabled is True
    assert settings.a2a_rate_limit_window_seconds == 60.0
    assert settings.a2a_rate_limit_max_requests == 120
    assert settings.a2a_stream_max_bytes == 64 * 1024 * 1024
    assert settings.a2a_stream_max_duration_seconds == 3600.0
    assert settings.a2a_stream_idle_timeout_seconds == 120.0


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
        "A2A_RATE_LIMIT_ENABLED": "false",
        "A2A_METRICS_ENABLED": "false",
        "A2A_RATE_LIMIT_WINDOW_SECONDS": "30",
        "A2A_RATE_LIMIT_MAX_REQUESTS": "45",
        "A2A_STREAM_MAX_BYTES": "1048576",
        "A2A_STREAM_MAX_DURATION_SECONDS": "900",
        "A2A_STREAM_IDLE_TIMEOUT_SECONDS": "45",
        "A2A_PENDING_SESSION_CLAIM_TTL_SECONDS": "45",
        "A2A_INTERRUPT_REQUEST_TTL_SECONDS": "7200",
        "A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS": "120",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "0.75",
        "A2A_ENABLE_SESSION_SHELL": "true",
        "OPENCODE_MAX_CONCURRENT_REQUESTS": "12",
        "OPENCODE_MAX_CONCURRENT_STREAMS": "3",
        "OPENCODE_AUTH_USERNAME": "service",
        "OPENCODE_AUTH_PASSWORD": "s3cret",  # pragma: allowlist secret
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
        assert settings.a2a_rate_limit_enabled is False
        assert settings.a2a_metrics_enabled is False
        assert settings.a2a_rate_limit_window_seconds == 30.0
        assert settings.a2a_rate_limit_max_requests == 45
        assert settings.a2a_stream_max_bytes == 1_048_576
        assert settings.a2a_stream_max_duration_seconds == 900.0
        assert settings.a2a_stream_idle_timeout_seconds == 45.0
        assert settings.a2a_pending_session_claim_ttl_seconds == 45.0
        assert settings.a2a_interrupt_request_ttl_seconds == 7200.0
        assert settings.a2a_interrupt_request_tombstone_ttl_seconds == 120.0
        assert settings.a2a_cancel_abort_timeout_seconds == 0.75
        assert settings.opencode_max_concurrent_requests == 12
        assert settings.opencode_max_concurrent_streams == 3
        assert settings.opencode_auth_username == "service"
        assert settings.opencode_auth_password == "s3cret"  # pragma: allowlist secret
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
        assert A2A_PROTOCOL_VERSION == "1.0"
        assert A2A_SUPPORTED_PROTOCOL_VERSIONS == ("1.0",)


def test_settings_opencode_auth_defaults_to_opencode_username_without_password() -> None:
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
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings()

    assert settings.opencode_auth_username == "opencode"
    assert settings.opencode_auth_password is None


def test_settings_opencode_auth_repr_hides_password() -> None:
    settings = make_settings(
        opencode_auth_username="opencode",
        opencode_auth_password="hunter2-secret",  # pragma: allowlist secret
    )

    assert "hunter2-secret" not in repr(settings)
    assert "opencode_auth_password" not in repr(settings)


def test_make_settings_ignores_ambient_environment_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENCODE_BASE_URL=http://dotenv-upstream.test",
                "A2A_PUBLIC_URL=http://dotenv-public.test",
                "A2A_HOST=dotenv-host",
            ]
        ),
        encoding="utf-8",
    )

    with mock.patch.dict(
        os.environ,
        {
            "OPENCODE_BASE_URL": "http://env-upstream.test",
            "A2A_PUBLIC_URL": "http://env-public.test",
            "A2A_HOST": "env-host",
        },
        clear=False,
    ):
        settings = make_settings()

    assert settings.opencode_base_url == "http://127.0.0.1:4096"
    assert settings.a2a_public_url == "http://127.0.0.1:8000"
    assert settings.a2a_host == "127.0.0.1"


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


def test_settings_reject_non_sqlite_database_url() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_settings(a2a_task_store_database_url="postgresql+asyncpg://db.example.com/app")

    assert "A2A_TASK_STORE_DATABASE_URL must use the sqlite+aiosqlite scheme" in str(excinfo.value)


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


def test_settings_reject_invalid_admission_limits() -> None:
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
        "A2A_RATE_LIMIT_WINDOW_SECONDS": "0",
        "A2A_RATE_LIMIT_MAX_REQUESTS": "0",
        "A2A_STREAM_MAX_BYTES": "-1",
        "A2A_STREAM_MAX_DURATION_SECONDS": "-1",
        "A2A_STREAM_IDLE_TIMEOUT_SECONDS": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings()

    field_names = {e["loc"][0] for e in excinfo.value.errors()}
    assert {"A2A_RATE_LIMIT_WINDOW_SECONDS", "A2A_RATE_LIMIT_MAX_REQUESTS"} <= field_names
    assert {"A2A_STREAM_MAX_BYTES", "A2A_STREAM_MAX_DURATION_SECONDS"} <= field_names
    assert "A2A_STREAM_IDLE_TIMEOUT_SECONDS" in field_names


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

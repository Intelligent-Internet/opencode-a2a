import os
from unittest import mock

import pytest
from pydantic import ValidationError

from opencode_a2a_server import __version__
from opencode_a2a_server.config import Settings


def test_settings_missing_required():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings.from_env()
        # Should mention missing required fields
        errors = excinfo.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "A2A_BEARER_TOKEN" in field_names


def test_settings_valid():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_TIMEOUT": "300",
        "OPENCODE_MANAGED_SERVER": "true",
        "OPENCODE_MANAGED_SERVER_HOST": "127.0.0.1",
        "OPENCODE_MANAGED_SERVER_PORT": "42111",
        "OPENCODE_COMMAND": "/usr/local/bin/opencode",
        "OPENCODE_STARTUP_TIMEOUT": "9.5",
        "OPENCODE_WORKSPACE_ROOT": "/srv/workspaces/alpha",
        "A2A_MAX_REQUEST_BODY_BYTES": "2048",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "0.75",
        "A2A_ENABLE_SESSION_SHELL": "true",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_bearer_token == "test-token"
        assert settings.opencode_timeout == 300.0
        assert settings.opencode_managed_server is True
        assert settings.opencode_managed_server_host == "127.0.0.1"
        assert settings.opencode_managed_server_port == 42111
        assert settings.opencode_command == "/usr/local/bin/opencode"
        assert settings.opencode_startup_timeout == 9.5
        assert settings.opencode_workspace_root == "/srv/workspaces/alpha"
        assert settings.a2a_max_request_body_bytes == 2048
        assert settings.a2a_cancel_abort_timeout_seconds == 0.75
        assert settings.a2a_enable_session_shell is True
        assert settings.a2a_version == __version__


def test_settings_ignore_legacy_opencode_directory_env() -> None:
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_DIRECTORY": "/legacy/workspace",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()

    assert settings.opencode_workspace_root is None


def test_settings_reject_negative_max_request_body_bytes():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "A2A_MAX_REQUEST_BODY_BYTES": "-1",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValidationError) as excinfo:
            Settings.from_env()

    field_names = [e["loc"][0] for e in excinfo.value.errors()]
    assert "A2A_MAX_REQUEST_BODY_BYTES" in field_names

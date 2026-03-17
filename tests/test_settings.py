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
        "A2A_MAX_REQUEST_BODY_BYTES": "2048",
        "A2A_CANCEL_ABORT_TIMEOUT_SECONDS": "0.75",
        "A2A_ENABLE_SESSION_SHELL": "true",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_bearer_token == "test-token"
        assert settings.opencode_timeout == 300.0
        assert settings.a2a_max_request_body_bytes == 2048
        assert settings.a2a_cancel_abort_timeout_seconds == 0.75
        assert settings.a2a_enable_session_shell is True
        assert settings.a2a_version == __version__


def test_parse_oauth_scopes():
    env = {
        "A2A_BEARER_TOKEN": "test",
        "A2A_OAUTH_SCOPES": "scope1, scope2,,scope3 ",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_oauth_scopes == {"scope1": "", "scope2": "", "scope3": ""}


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

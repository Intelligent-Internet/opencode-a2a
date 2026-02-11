import os
from unittest import mock

from opencode_a2a.config import Settings


def test_settings_missing_required():
    with mock.patch.dict(os.environ, {}, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_bearer_token is None
        assert settings.a2a_jwt_secret is None


def test_settings_valid():
    env = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_TIMEOUT": "300",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_bearer_token == "test-token"
        assert settings.opencode_timeout == 300.0


def test_parse_required_scopes():
    env = {
        "A2A_REQUIRED_SCOPES": "scope1, scope2,,scope3 ",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        settings = Settings.from_env()
        assert settings.a2a_required_scopes == {"scope1", "scope2", "scope3"}

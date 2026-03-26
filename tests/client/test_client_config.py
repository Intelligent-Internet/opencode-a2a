from __future__ import annotations

from base64 import b64encode

import pytest

from opencode_a2a.client.config import A2AClientSettings, load_settings


def test_load_settings_default() -> None:
    settings = load_settings({})

    assert settings == A2AClientSettings()


def test_load_settings_from_mapping() -> None:
    raw = {
        "A2A_CLIENT_TIMEOUT_SECONDS": "42",
        "A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS": 6,
        "A2A_CLIENT_USE_CLIENT_PREFERENCE": "true",
        "A2A_CLIENT_BEARER_TOKEN": "peer-token",
        "A2A_CLIENT_BASIC_AUTH": "user:pass",
        "A2A_CLIENT_SUPPORTED_TRANSPORTS": "json-rpc,http-json",
    }

    settings = load_settings(raw)

    assert settings.default_timeout == 42.0
    assert settings.card_fetch_timeout == 6.0
    assert settings.use_client_preference is True
    assert settings.bearer_token == "peer-token"
    assert settings.basic_auth == "user:pass"
    assert settings.supported_transports == ("JSONRPC", "HTTP+JSON")


def test_load_settings_invalid_transport_raises() -> None:
    with pytest.raises(ValueError, match="supported_transports"):
        load_settings({"A2A_CLIENT_SUPPORTED_TRANSPORTS": 1})


def test_load_settings_invalid_bool_raises() -> None:
    with pytest.raises(ValueError, match="boolean-like"):
        load_settings({"A2A_CLIENT_USE_CLIENT_PREFERENCE": "maybe"})


def test_load_settings_invalid_bearer_token_type_raises() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        load_settings({"A2A_CLIENT_BEARER_TOKEN": 123})


def test_load_settings_accepts_base64_basic_auth() -> None:
    encoded = b64encode(b"user:pass").decode()

    settings = load_settings({"A2A_CLIENT_BASIC_AUTH": encoded})

    assert settings == A2AClientSettings(basic_auth=encoded)


def test_load_settings_invalid_basic_auth_raises() -> None:
    with pytest.raises(ValueError, match="username:password"):
        load_settings({"A2A_CLIENT_BASIC_AUTH": "not-basic-auth"})

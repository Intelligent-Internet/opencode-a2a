from __future__ import annotations

import tempfile
import uuid
from typing import Any

from pydantic_settings import PydanticBaseSettingsSource

from opencode_a2a.config import Settings


class _IsolatedTestSettings(Settings):
    """Test-only settings variant that ignores ambient env and .env sources."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[Settings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)


def _build_test_static_auth_credentials(**overrides: Any) -> tuple[dict[str, Any], ...]:
    explicit_credentials = overrides.pop("a2a_static_auth_credentials", None)
    has_bearer_override = "test_bearer_token" in overrides
    has_basic_username_override = "test_basic_username" in overrides
    has_basic_password_override = "test_basic_password" in overrides  # pragma: allowlist secret
    bearer_token = overrides.pop("test_bearer_token", "test-token")
    basic_username = overrides.pop("test_basic_username", None)
    basic_password = overrides.pop("test_basic_password", None)  # pragma: allowlist secret

    if explicit_credentials is not None:
        if (
            (has_bearer_override and bearer_token is not None)
            or (has_basic_username_override and basic_username is not None)
            or (has_basic_password_override and basic_password is not None)
        ):
            raise ValueError(
                "Test settings helper does not combine a2a_static_auth_credentials "
                "with shorthand auth overrides."
            )
        return tuple(explicit_credentials)

    credentials: list[dict[str, Any]] = []
    if bearer_token is not None:
        credentials.append(
            {
                "scheme": "bearer",
                "token": bearer_token,
                "principal": "automation",
            }
        )
    if basic_username is not None or basic_password is not None:
        if not basic_username or not basic_password:
            raise ValueError(
                "Test settings helper requires both basic username and password overrides."
            )
        credentials.append(
            {
                "scheme": "basic",
                "username": basic_username,
                "password": basic_password,
            }
        )
    return tuple(credentials)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "opencode_base_url": "http://127.0.0.1:4096",
        "a2a_task_store_database_url": (
            f"sqlite+aiosqlite:///{tempfile.gettempdir()}/opencode-a2a-test-{uuid.uuid4().hex}.db"
        ),
    }
    base.update(overrides)
    base["a2a_static_auth_credentials"] = _build_test_static_auth_credentials(**base)
    return _IsolatedTestSettings(**base)

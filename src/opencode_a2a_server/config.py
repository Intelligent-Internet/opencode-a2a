from __future__ import annotations

from typing import cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from opencode_a2a_server import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # OpenCode settings
    opencode_base_url: str = Field(default="http://127.0.0.1:4096", alias="OPENCODE_BASE_URL")
    opencode_managed_server: bool = Field(default=False, alias="OPENCODE_MANAGED_SERVER")
    opencode_managed_server_host: str = Field(
        default="127.0.0.1",
        alias="OPENCODE_MANAGED_SERVER_HOST",
    )
    opencode_managed_server_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        alias="OPENCODE_MANAGED_SERVER_PORT",
    )
    opencode_command: str = Field(default="opencode", alias="OPENCODE_COMMAND")
    opencode_startup_timeout: float = Field(
        default=20.0,
        gt=0.0,
        alias="OPENCODE_STARTUP_TIMEOUT",
    )
    opencode_workspace_root: str | None = Field(default=None, alias="OPENCODE_WORKSPACE_ROOT")
    opencode_provider_id: str | None = Field(default=None, alias="OPENCODE_PROVIDER_ID")
    opencode_model_id: str | None = Field(default=None, alias="OPENCODE_MODEL_ID")
    opencode_agent: str | None = Field(default=None, alias="OPENCODE_AGENT")
    opencode_system: str | None = Field(default=None, alias="OPENCODE_SYSTEM")
    opencode_variant: str | None = Field(default=None, alias="OPENCODE_VARIANT")
    opencode_timeout: float = Field(default=120.0, alias="OPENCODE_TIMEOUT")
    opencode_timeout_stream: float | None = Field(default=None, alias="OPENCODE_TIMEOUT_STREAM")

    # A2A settings
    a2a_public_url: str = Field(default="http://127.0.0.1:8000", alias="A2A_PUBLIC_URL")
    a2a_project: str | None = Field(default=None, alias="A2A_PROJECT")
    a2a_title: str = Field(default="OpenCode A2A", alias="A2A_TITLE")
    a2a_description: str = Field(
        default="A2A wrapper service for OpenCode", alias="A2A_DESCRIPTION"
    )
    a2a_version: str = Field(default=__version__, alias="A2A_VERSION")
    a2a_protocol_version: str = Field(default="0.3.0", alias="A2A_PROTOCOL_VERSION")
    a2a_log_level: str = Field(default="WARNING", alias="A2A_LOG_LEVEL")
    a2a_log_payloads: bool = Field(default=False, alias="A2A_LOG_PAYLOADS")
    a2a_log_body_limit: int = Field(default=0, alias="A2A_LOG_BODY_LIMIT")
    a2a_max_request_body_bytes: int = Field(
        default=1_048_576,
        ge=0,
        alias="A2A_MAX_REQUEST_BODY_BYTES",
    )
    a2a_documentation_url: str | None = Field(default=None, alias="A2A_DOCUMENTATION_URL")
    a2a_allow_directory_override: bool = Field(default=True, alias="A2A_ALLOW_DIRECTORY_OVERRIDE")
    a2a_enable_session_shell: bool = Field(default=False, alias="A2A_ENABLE_SESSION_SHELL")
    a2a_host: str = Field(default="127.0.0.1", alias="A2A_HOST")
    a2a_port: int = Field(default=8000, alias="A2A_PORT")
    a2a_bearer_token: str = Field(..., min_length=1, alias="A2A_BEARER_TOKEN")

    # Session cache settings
    a2a_session_cache_ttl_seconds: int = Field(default=3600, alias="A2A_SESSION_CACHE_TTL_SECONDS")
    a2a_session_cache_maxsize: int = Field(default=10_000, alias="A2A_SESSION_CACHE_MAXSIZE")
    a2a_cancel_abort_timeout_seconds: float = Field(
        default=2.0,
        ge=0.0,
        alias="A2A_CANCEL_ABORT_TIMEOUT_SECONDS",
    )

    @classmethod
    def from_env(cls) -> Settings:
        # BaseSettings constructor loads values from env and applies validation.
        settings_cls: type[BaseSettings] = cls
        return cast(Settings, settings_cls())

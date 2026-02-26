from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, cast

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_AUTH_MODES = {"bearer", "jwt"}


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
    opencode_directory: str | None = Field(default=None, alias="OPENCODE_DIRECTORY")
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
    a2a_version: str = Field(default="0.1.0", alias="A2A_VERSION")
    a2a_protocol_version: str = Field(default="0.3.0", alias="A2A_PROTOCOL_VERSION")
    a2a_streaming: bool = Field(default=True, alias="A2A_STREAMING")
    a2a_log_level: str = Field(default="WARNING", alias="A2A_LOG_LEVEL")
    a2a_log_payloads: bool = Field(default=False, alias="A2A_LOG_PAYLOADS")
    a2a_log_body_limit: int = Field(default=0, alias="A2A_LOG_BODY_LIMIT")
    a2a_documentation_url: str | None = Field(default=None, alias="A2A_DOCUMENTATION_URL")
    a2a_allow_directory_override: bool = Field(default=True, alias="A2A_ALLOW_DIRECTORY_OVERRIDE")
    a2a_enable_session_shell: bool = Field(default=False, alias="A2A_ENABLE_SESSION_SHELL")
    a2a_host: str = Field(default="127.0.0.1", alias="A2A_HOST")
    a2a_port: int = Field(default=8000, alias="A2A_PORT")
    a2a_auth_mode: str = Field(default="bearer", alias="A2A_AUTH_MODE")
    a2a_bearer_token: str | None = Field(default=None, alias="A2A_BEARER_TOKEN")

    # JWT settings (used when A2A_AUTH_MODE=jwt)
    a2a_jwt_secret: str | None = Field(default=None, alias="A2A_JWT_SECRET")
    a2a_jwt_secret_b64: str | None = Field(default=None, alias="A2A_JWT_SECRET_B64")
    a2a_jwt_secret_file: str | None = Field(default=None, alias="A2A_JWT_SECRET_FILE")
    a2a_jwt_algorithm: str = Field(default="HS256", alias="A2A_JWT_ALGORITHM")
    a2a_jwt_issuer: str | None = Field(default=None, alias="A2A_JWT_ISSUER")
    a2a_jwt_audience: str | None = Field(default=None, alias="A2A_JWT_AUDIENCE")
    a2a_required_scopes: set[str] = Field(default_factory=set, alias="A2A_REQUIRED_SCOPES")
    a2a_jwt_scope_match: str = Field(default="any", alias="A2A_JWT_SCOPE_MATCH")

    # OAuth2 settings
    a2a_oauth_authorization_url: str | None = Field(
        default=None, alias="A2A_OAUTH_AUTHORIZATION_URL"
    )
    a2a_oauth_token_url: str | None = Field(default=None, alias="A2A_OAUTH_TOKEN_URL")
    a2a_oauth_metadata_url: str | None = Field(default=None, alias="A2A_OAUTH_METADATA_URL")
    a2a_oauth_scopes: Any = Field(default_factory=dict, alias="A2A_OAUTH_SCOPES")

    # Session cache settings
    a2a_session_cache_ttl_seconds: int = Field(default=3600, alias="A2A_SESSION_CACHE_TTL_SECONDS")
    a2a_session_cache_maxsize: int = Field(default=10_000, alias="A2A_SESSION_CACHE_MAXSIZE")
    a2a_cancel_abort_timeout_seconds: float = Field(
        default=2.0,
        ge=0.0,
        alias="A2A_CANCEL_ABORT_TIMEOUT_SECONDS",
    )

    @field_validator("a2a_oauth_scopes", mode="before")
    @classmethod
    def parse_oauth_scopes(cls, v: Any) -> dict[str, str]:
        if isinstance(v, dict):
            return v
        if not isinstance(v, str) or not v:
            return {}
        scopes: dict[str, str] = {}
        for raw in v.split(","):
            scope = raw.strip()
            if scope:
                scopes[scope] = ""
        return scopes

    @field_validator("a2a_auth_mode", mode="before")
    @classmethod
    def normalize_auth_mode(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            return "bearer"
        mode = v.strip().lower()
        if mode in _AUTH_MODES:
            return mode
        raise ValueError(f"A2A_AUTH_MODE must be one of {sorted(_AUTH_MODES)}")

    @field_validator("a2a_jwt_scope_match", mode="before")
    @classmethod
    def normalize_jwt_scope_match(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            return "any"
        return v.strip().lower()

    @field_validator("a2a_required_scopes", mode="before")
    @classmethod
    def parse_required_scopes(cls, v: Any) -> set[str]:
        if v is None:
            return set()
        if isinstance(v, set):
            return {str(item).strip() for item in v if str(item).strip()}
        if isinstance(v, (list, tuple)):
            return {str(item).strip() for item in v if str(item).strip()}
        if isinstance(v, str):
            return {item.strip() for item in v.split(",") if item.strip()}
        return set()

    @model_validator(mode="after")
    def resolve_auth_settings(self) -> Settings:
        if self.a2a_auth_mode == "bearer":
            if not self.a2a_bearer_token:
                raise ValueError("A2A_BEARER_TOKEN is required when A2A_AUTH_MODE=bearer")
            return self

        if self.a2a_jwt_scope_match not in {"any", "all"}:
            raise ValueError("A2A_JWT_SCOPE_MATCH must be 'any' or 'all'")

        if self.a2a_jwt_secret_b64:
            try:
                decoded = base64.b64decode(self.a2a_jwt_secret_b64.strip(), validate=True)
                self.a2a_jwt_secret = decoded.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("A2A_JWT_SECRET_B64 must be valid base64-encoded text") from exc
        elif self.a2a_jwt_secret_file and not self.a2a_jwt_secret:
            self.a2a_jwt_secret = (
                Path(self.a2a_jwt_secret_file).expanduser().read_text(encoding="utf-8")
            )

        if not self.a2a_jwt_secret:
            raise ValueError(
                "JWT mode requires one of A2A_JWT_SECRET/A2A_JWT_SECRET_B64/A2A_JWT_SECRET_FILE"
            )
        if not self.a2a_jwt_issuer:
            raise ValueError("JWT mode requires A2A_JWT_ISSUER")
        if not self.a2a_jwt_audience:
            raise ValueError("JWT mode requires A2A_JWT_AUDIENCE")
        return self

    @classmethod
    def from_env(cls) -> Settings:
        # BaseSettings constructor loads values from env and applies validation.
        settings_cls: type[BaseSettings] = cls
        return cast(Settings, settings_cls())

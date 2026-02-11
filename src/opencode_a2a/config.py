from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    a2a_title: str = Field(default="OpenCode A2A", alias="A2A_TITLE")
    a2a_description: str = Field(
        default="A2A wrapper service for OpenCode", alias="A2A_DESCRIPTION"
    )
    a2a_version: str = Field(default="0.1.0", alias="A2A_VERSION")
    a2a_protocol_version: str = Field(default="0.3.0", alias="A2A_PROTOCOL_VERSION")
    a2a_streaming: bool = Field(default=True, alias="A2A_STREAMING")
    a2a_log_level: str = Field(default="INFO", alias="A2A_LOG_LEVEL")
    a2a_log_payloads: bool = Field(default=False, alias="A2A_LOG_PAYLOADS")
    a2a_log_body_limit: int = Field(default=0, alias="A2A_LOG_BODY_LIMIT")
    a2a_documentation_url: str | None = Field(default=None, alias="A2A_DOCUMENTATION_URL")
    a2a_allow_directory_override: bool = Field(default=True, alias="A2A_ALLOW_DIRECTORY_OVERRIDE")
    a2a_host: str = Field(default="127.0.0.1", alias="A2A_HOST")
    a2a_port: int = Field(default=8000, alias="A2A_PORT")

    # Legacy bearer env kept as optional for compatibility with older scripts/tests.
    a2a_bearer_token: str | None = Field(default=None, alias="A2A_BEARER_TOKEN")

    # JWT settings (active auth mode)
    a2a_jwt_secret: str | None = Field(default=None, alias="A2A_JWT_SECRET")
    a2a_jwt_secret_b64: str | None = Field(default=None, alias="A2A_JWT_SECRET_B64")
    a2a_jwt_secret_file: str | None = Field(default=None, alias="A2A_JWT_SECRET_FILE")
    a2a_jwt_algorithm: str = Field(default="RS256", alias="A2A_JWT_ALGORITHM")
    a2a_jwt_issuer: str | None = Field(default=None, alias="A2A_JWT_ISSUER")
    a2a_jwt_audience: str | None = Field(default=None, alias="A2A_JWT_AUDIENCE")
    a2a_required_scopes: Annotated[set[str], NoDecode] = Field(
        default_factory=set, alias="A2A_REQUIRED_SCOPES"
    )
    a2a_jwt_scope_match: str = Field(default="any", alias="A2A_JWT_SCOPE_MATCH")

    # Session cache settings
    a2a_session_cache_ttl_seconds: int = Field(default=3600, alias="A2A_SESSION_CACHE_TTL_SECONDS")
    a2a_session_cache_maxsize: int = Field(default=10_000, alias="A2A_SESSION_CACHE_MAXSIZE")

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

    @field_validator("a2a_jwt_scope_match", mode="before")
    @classmethod
    def normalize_scope_match(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            return "any"
        return v.strip().lower()

    @model_validator(mode="after")
    def resolve_jwt_secret(self) -> Settings:
        if self.a2a_jwt_secret_b64:
            try:
                decoded = base64.b64decode(self.a2a_jwt_secret_b64.strip(), validate=True)
            except ValueError as exc:
                raise ValueError("A2A_JWT_SECRET_B64 must be valid base64") from exc
            self.a2a_jwt_secret = decoded.decode("utf-8")
            return self

        if self.a2a_jwt_secret_file:
            self.a2a_jwt_secret = (
                Path(self.a2a_jwt_secret_file).expanduser().read_text(encoding="utf-8")
            )
            return self

        return self

    @classmethod
    def from_env(cls) -> Settings:
        return cls()

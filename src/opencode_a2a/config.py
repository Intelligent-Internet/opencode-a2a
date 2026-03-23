from __future__ import annotations

import json
from typing import Annotated, Any, Literal, cast

from pydantic import BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from opencode_a2a import __version__
from opencode_a2a.sandbox_policy import SandboxPolicy

SandboxMode = Literal[
    "unknown",
    "read-only",
    "workspace-write",
    "danger-full-access",
    "custom",
]
SandboxFilesystemScope = Literal[
    "unknown",
    "workspace_only",
    "workspace_and_declared_roots",
    "unrestricted",
    "custom",
]
NetworkAccess = Literal["unknown", "disabled", "enabled", "restricted", "custom"]
ApprovalPolicy = Literal["unknown", "never", "on-request", "on-failure", "untrusted", "custom"]
EscalationBehavior = Literal["unknown", "manual", "automatic", "unsupported", "custom"]
WriteAccessScope = Literal[
    "unknown",
    "none",
    "workspace_only",
    "workspace_and_declared_roots",
    "unrestricted",
    "custom",
]
OutsideWorkspaceAccess = Literal["unknown", "allowed", "disallowed", "custom"]


def _parse_declared_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            else:
                if not isinstance(parsed, list):
                    raise TypeError("Expected a JSON array for declared list values.")
                return tuple(str(item).strip() for item in parsed if str(item).strip())
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    raise TypeError("Expected a comma-separated string, JSON array, or sequence.")


DeclaredStringList = Annotated[tuple[str, ...], NoDecode, BeforeValidator(_parse_declared_list)]


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
    opencode_workspace_root: str | None = Field(default=None, alias="OPENCODE_WORKSPACE_ROOT")
    opencode_agent: str | None = Field(default=None, alias="OPENCODE_AGENT")
    opencode_system: str | None = Field(default=None, alias="OPENCODE_SYSTEM")
    opencode_variant: str | None = Field(default=None, alias="OPENCODE_VARIANT")
    opencode_timeout: float = Field(default=120.0, alias="OPENCODE_TIMEOUT")
    opencode_timeout_stream: float | None = Field(default=None, alias="OPENCODE_TIMEOUT_STREAM")

    # A2A settings
    a2a_public_url: str = Field(default="http://127.0.0.1:8000", alias="A2A_PUBLIC_URL")
    a2a_project: str | None = Field(default=None, alias="A2A_PROJECT")
    a2a_title: str = Field(default="OpenCode A2A", alias="A2A_TITLE")
    a2a_description: str = Field(default="OpenCode A2A runtime", alias="A2A_DESCRIPTION")
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
    a2a_sandbox_mode: SandboxMode = Field(default="unknown", alias="A2A_SANDBOX_MODE")
    a2a_sandbox_filesystem_scope: SandboxFilesystemScope = Field(
        default="unknown",
        alias="A2A_SANDBOX_FILESYSTEM_SCOPE",
    )
    a2a_sandbox_writable_roots: DeclaredStringList = Field(
        default=(),
        alias="A2A_SANDBOX_WRITABLE_ROOTS",
    )
    a2a_network_access: NetworkAccess = Field(default="unknown", alias="A2A_NETWORK_ACCESS")
    a2a_network_allowed_domains: DeclaredStringList = Field(
        default=(),
        alias="A2A_NETWORK_ALLOWED_DOMAINS",
    )
    a2a_approval_policy: ApprovalPolicy = Field(
        default="unknown",
        alias="A2A_APPROVAL_POLICY",
    )
    a2a_approval_escalation_behavior: EscalationBehavior = Field(
        default="unknown",
        alias="A2A_APPROVAL_ESCALATION_BEHAVIOR",
    )
    a2a_write_access_scope: WriteAccessScope = Field(
        default="unknown",
        alias="A2A_WRITE_ACCESS_SCOPE",
    )
    a2a_write_access_outside_workspace: OutsideWorkspaceAccess = Field(
        default="unknown",
        alias="A2A_WRITE_ACCESS_OUTSIDE_WORKSPACE",
    )
    a2a_host: str = Field(default="127.0.0.1", alias="A2A_HOST")
    a2a_port: int = Field(default=8000, alias="A2A_PORT")
    a2a_bearer_token: str = Field(..., min_length=1, alias="A2A_BEARER_TOKEN")

    # Session cache settings
    a2a_session_cache_ttl_seconds: int = Field(default=3600, alias="A2A_SESSION_CACHE_TTL_SECONDS")
    a2a_session_cache_maxsize: int = Field(default=10_000, alias="A2A_SESSION_CACHE_MAXSIZE")
    a2a_interrupt_request_ttl_seconds: float = Field(
        default=10_800.0,
        ge=0.0,
        alias="A2A_INTERRUPT_REQUEST_TTL_SECONDS",
    )
    a2a_interrupt_request_tombstone_ttl_seconds: float = Field(
        default=600.0,
        ge=0.0,
        alias="A2A_INTERRUPT_REQUEST_TOMBSTONE_TTL_SECONDS",
    )
    a2a_cancel_abort_timeout_seconds: float = Field(
        default=2.0,
        ge=0.0,
        alias="A2A_CANCEL_ABORT_TIMEOUT_SECONDS",
    )

    # Outbound A2A Client settings
    a2a_client_timeout_seconds: float = Field(default=30.0, alias="A2A_CLIENT_TIMEOUT_SECONDS")
    a2a_client_card_fetch_timeout_seconds: float = Field(
        default=5.0,
        alias="A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS",
    )
    a2a_client_use_client_preference: bool = Field(
        default=False, alias="A2A_CLIENT_USE_CLIENT_PREFERENCE"
    )
    a2a_client_bearer_token: str | None = Field(default=None, alias="A2A_CLIENT_BEARER_TOKEN")
    a2a_client_cache_ttl_seconds: float = Field(
        default=900.0,
        ge=0.0,
        alias="A2A_CLIENT_CACHE_TTL_SECONDS",
    )
    a2a_client_cache_maxsize: int = Field(
        default=256,
        ge=0,
        alias="A2A_CLIENT_CACHE_MAXSIZE",
    )
    a2a_client_supported_transports: DeclaredStringList = Field(
        default=("JSONRPC", "HTTP+JSON"),
        alias="A2A_CLIENT_SUPPORTED_TRANSPORTS",
    )

    @model_validator(mode="after")
    def _validate_sandbox_policy(self) -> Settings:
        SandboxPolicy.from_settings(self).validate_configuration()
        return self

    @classmethod
    def from_env(cls) -> Settings:
        settings_cls: type[BaseSettings] = cls
        return cast(Settings, settings_cls())

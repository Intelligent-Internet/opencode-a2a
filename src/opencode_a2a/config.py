from __future__ import annotations

import os
from dataclasses import dataclass


def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _get_int(name: str, default: int) -> int:
    value = _get_env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = _get_env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    opencode_base_url: str
    opencode_directory: str | None
    opencode_provider_id: str | None
    opencode_model_id: str | None
    opencode_agent: str | None
    opencode_system: str | None
    opencode_variant: str | None
    opencode_timeout: float
    a2a_public_url: str
    a2a_title: str
    a2a_description: str
    a2a_version: str
    a2a_protocol_version: str
    a2a_host: str
    a2a_port: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            opencode_base_url=_get_env("OPENCODE_BASE_URL", "http://127.0.0.1:4096"),
            opencode_directory=_get_env("OPENCODE_DIRECTORY"),
            opencode_provider_id=_get_env("OPENCODE_PROVIDER_ID"),
            opencode_model_id=_get_env("OPENCODE_MODEL_ID"),
            opencode_agent=_get_env("OPENCODE_AGENT"),
            opencode_system=_get_env("OPENCODE_SYSTEM"),
            opencode_variant=_get_env("OPENCODE_VARIANT"),
            opencode_timeout=_get_float("OPENCODE_TIMEOUT", 120.0),
            a2a_public_url=_get_env("A2A_PUBLIC_URL", "http://127.0.0.1:8000"),
            a2a_title=_get_env("A2A_TITLE", "OpenCode A2A"),
            a2a_description=_get_env("A2A_DESCRIPTION", "A2A wrapper service for OpenCode"),
            a2a_version=_get_env("A2A_VERSION", "0.1.0"),
            a2a_protocol_version=_get_env("A2A_PROTOCOL_VERSION", "0.3.0"),
            a2a_host=_get_env("A2A_HOST", "127.0.0.1"),
            a2a_port=_get_int("A2A_PORT", 8000),
        )

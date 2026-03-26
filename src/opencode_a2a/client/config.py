"""Configuration helpers for the opencode-a2a A2A client initialization layer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .auth import validate_basic_auth
from .polling import PollingFallbackPolicy, validate_polling_fallback_policy


def _read_setting(
    source: Any,
    keys: Iterable[str],
    *,
    default: Any = None,
) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        for key in keys:
            if key in source:
                return source[key]
        return default
    for key in keys:
        if hasattr(source, key):
            return getattr(source, key)
    return default


def _coerce_float(name: str, value: Any, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number, got {value!r}") from exc
    raise ValueError(f"{name} must be a number, got {value!r}")


def _coerce_bool(name: str, value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        if lowered in {"t", "f"}:
            return lowered == "t"
    raise ValueError(f"{name} must be a boolean-like value, got {value!r}")


def _coerce_optional_str(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    raise ValueError(f"{name} must be a string, got {value!r}")


def _normalize_transport(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"jsonrpc", "json-rpc", "json_rpc"}:
        return "JSONRPC"
    if normalized in {"http+json", "http_json", "http-json", "httpjson", "http+json+"}:
        return "HTTP+JSON"
    if normalized in {"grpc"}:
        return "GRPC"
    if not normalized:
        return "JSONRPC"
    return value.strip()


def _parse_transports(
    raw_value: Any,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if raw_value is None:
        return default
    if isinstance(raw_value, str):
        items = [part for part in raw_value.split(",") if part.strip()]
    elif isinstance(raw_value, (list, tuple, set)):
        items = [str(part) for part in raw_value]
    else:
        raise ValueError("supported_transports must be a comma-separated string or list")

    normalized = tuple(_normalize_transport(item) for item in items if str(item).strip())
    return normalized or default


@dataclass(frozen=True)
class A2AClientSettings:
    """Runtime settings used by opencode-a2a client wrappers."""

    default_timeout: float = 30.0
    use_client_preference: bool = False
    card_fetch_timeout: float = 5.0
    bearer_token: str | None = None
    basic_auth: str | None = None
    supported_transports: tuple[str, ...] = (
        "JSONRPC",
        "HTTP+JSON",
    )
    polling_fallback_enabled: bool = False
    polling_fallback_initial_interval_seconds: float = 0.5
    polling_fallback_max_interval_seconds: float = 2.0
    polling_fallback_backoff_multiplier: float = 2.0
    polling_fallback_timeout_seconds: float = 10.0


def load_settings(raw_settings: Any) -> A2AClientSettings:
    """Load client settings from an object or mapping."""

    default_timeout = _coerce_float(
        "A2A_CLIENT_TIMEOUT_SECONDS",
        _read_setting(
            raw_settings,
            keys=("A2A_CLIENT_TIMEOUT_SECONDS", "a2a_client_timeout_seconds"),
            default=30.0,
        ),
        default=30.0,
    )
    card_fetch_timeout = _coerce_float(
        "A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS",
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS",
                "a2a_client_card_fetch_timeout_seconds",
            ),
            default=5.0,
        ),
        default=5.0,
    )
    use_client_preference = _coerce_bool(
        "A2A_CLIENT_USE_CLIENT_PREFERENCE",
        _read_setting(
            raw_settings,
            keys=("A2A_CLIENT_USE_CLIENT_PREFERENCE", "a2a_client_use_client_preference"),
            default=False,
        ),
        default=False,
    )
    bearer_token = _coerce_optional_str(
        "A2A_CLIENT_BEARER_TOKEN",
        _read_setting(
            raw_settings,
            keys=("A2A_CLIENT_BEARER_TOKEN", "a2a_client_bearer_token"),
            default=None,
        ),
    )
    basic_auth = _coerce_optional_str(
        "A2A_CLIENT_BASIC_AUTH",
        _read_setting(
            raw_settings,
            keys=("A2A_CLIENT_BASIC_AUTH", "a2a_client_basic_auth"),
            default=None,
        ),
    )
    if basic_auth is not None:
        validate_basic_auth(basic_auth)
    supported_transports = _parse_transports(
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_SUPPORTED_TRANSPORTS",
                "a2a_client_supported_transports",
            ),
            default=("JSONRPC", "HTTP+JSON"),
        ),
        default=("JSONRPC", "HTTP+JSON"),
    )
    polling_fallback_enabled = _coerce_bool(
        "A2A_CLIENT_POLLING_FALLBACK_ENABLED",
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_POLLING_FALLBACK_ENABLED",
                "a2a_client_polling_fallback_enabled",
            ),
            default=False,
        ),
        default=False,
    )
    polling_fallback_initial_interval_seconds = _coerce_float(
        "A2A_CLIENT_POLLING_FALLBACK_INITIAL_INTERVAL_SECONDS",
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_POLLING_FALLBACK_INITIAL_INTERVAL_SECONDS",
                "a2a_client_polling_fallback_initial_interval_seconds",
            ),
            default=0.5,
        ),
        default=0.5,
    )
    polling_fallback_max_interval_seconds = _coerce_float(
        "A2A_CLIENT_POLLING_FALLBACK_MAX_INTERVAL_SECONDS",
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_POLLING_FALLBACK_MAX_INTERVAL_SECONDS",
                "a2a_client_polling_fallback_max_interval_seconds",
            ),
            default=2.0,
        ),
        default=2.0,
    )
    polling_fallback_backoff_multiplier = _coerce_float(
        "A2A_CLIENT_POLLING_FALLBACK_BACKOFF_MULTIPLIER",
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_POLLING_FALLBACK_BACKOFF_MULTIPLIER",
                "a2a_client_polling_fallback_backoff_multiplier",
            ),
            default=2.0,
        ),
        default=2.0,
    )
    polling_fallback_timeout_seconds = _coerce_float(
        "A2A_CLIENT_POLLING_FALLBACK_TIMEOUT_SECONDS",
        _read_setting(
            raw_settings,
            keys=(
                "A2A_CLIENT_POLLING_FALLBACK_TIMEOUT_SECONDS",
                "a2a_client_polling_fallback_timeout_seconds",
            ),
            default=10.0,
        ),
        default=10.0,
    )

    validate_polling_fallback_policy(
        PollingFallbackPolicy(
            enabled=polling_fallback_enabled,
            initial_interval_seconds=polling_fallback_initial_interval_seconds,
            max_interval_seconds=polling_fallback_max_interval_seconds,
            backoff_multiplier=polling_fallback_backoff_multiplier,
            timeout_seconds=polling_fallback_timeout_seconds,
        )
    )

    return A2AClientSettings(
        default_timeout=default_timeout,
        use_client_preference=use_client_preference,
        card_fetch_timeout=card_fetch_timeout,
        bearer_token=bearer_token,
        basic_auth=basic_auth,
        supported_transports=supported_transports,
        polling_fallback_enabled=polling_fallback_enabled,
        polling_fallback_initial_interval_seconds=polling_fallback_initial_interval_seconds,
        polling_fallback_max_interval_seconds=polling_fallback_max_interval_seconds,
        polling_fallback_backoff_multiplier=polling_fallback_backoff_multiplier,
        polling_fallback_timeout_seconds=polling_fallback_timeout_seconds,
    )


__all__ = ["A2AClientSettings", "load_settings"]

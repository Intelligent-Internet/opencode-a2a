from __future__ import annotations

from fastapi import FastAPI

from opencode_a2a.contracts.extensions import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
)

_BASE_SETTINGS = {
    "opencode_timeout": 1.0,
    "a2a_log_level": "DEBUG",
}
_ALL_EXTENSION_URIS = (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
)


def _session_meta(payload: dict) -> dict:
    return payload["metadata"]["shared"]["session"]


def _jsonrpc_app(app: FastAPI):
    target = getattr(app.state, "_jsonrpc_app", None)
    if target is not None:
        return target
    raise AssertionError("JSON-RPC app handle not found")


def _extension_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(headers or {})
    merged["A2A-Extensions"] = ",".join(sorted(_ALL_EXTENSION_URIS))
    return merged

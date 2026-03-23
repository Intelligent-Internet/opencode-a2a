from __future__ import annotations

from fastapi import FastAPI

_BASE_SETTINGS = {
    "opencode_timeout": 1.0,
    "a2a_log_level": "DEBUG",
}


def _session_meta(payload: dict) -> dict:
    return payload["metadata"]["shared"]["session"]


def _jsonrpc_app(app: FastAPI):
    target = getattr(app.state, "_jsonrpc_app", None)
    if target is not None:
        return target
    raise AssertionError("JSON-RPC app handle not found")

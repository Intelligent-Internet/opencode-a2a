from __future__ import annotations

_BASE_SETTINGS = {
    "opencode_timeout": 1.0,
    "a2a_log_level": "DEBUG",
}


def _session_meta(payload: dict) -> dict:
    return payload["metadata"]["shared"]["session"]

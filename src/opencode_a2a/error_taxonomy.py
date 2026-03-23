from __future__ import annotations

from dataclasses import dataclass

import httpx
from a2a.types import TaskState


@dataclass(frozen=True)
class UpstreamHTTPErrorProfile:
    error_type: str
    state: TaskState
    default_message: str


_UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS: dict[int, UpstreamHTTPErrorProfile] = {
    400: UpstreamHTTPErrorProfile(
        "UPSTREAM_BAD_REQUEST",
        TaskState.failed,
        "OpenCode rejected the request due to invalid input",
    ),
    401: UpstreamHTTPErrorProfile(
        "UPSTREAM_UNAUTHORIZED",
        TaskState.auth_required,
        "OpenCode rejected the request due to authentication failure",
    ),
    403: UpstreamHTTPErrorProfile(
        "UPSTREAM_PERMISSION_DENIED",
        TaskState.failed,
        "OpenCode rejected the request due to insufficient permissions",
    ),
    404: UpstreamHTTPErrorProfile(
        "UPSTREAM_RESOURCE_NOT_FOUND",
        TaskState.failed,
        "OpenCode rejected the request because the target resource was not found",
    ),
    429: UpstreamHTTPErrorProfile(
        "UPSTREAM_QUOTA_EXCEEDED",
        TaskState.failed,
        "OpenCode rejected the request due to quota limits",
    ),
}


def resolve_upstream_http_error_profile(status: int) -> UpstreamHTTPErrorProfile:
    if status in _UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS:
        return _UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS[status]
    if 400 <= status < 500:
        return UpstreamHTTPErrorProfile(
            "UPSTREAM_CLIENT_ERROR",
            TaskState.failed,
            f"OpenCode rejected the request with client error {status}",
        )
    if status >= 500:
        return UpstreamHTTPErrorProfile(
            "UPSTREAM_SERVER_ERROR",
            TaskState.failed,
            f"OpenCode rejected the request with server error {status}",
        )
    return UpstreamHTTPErrorProfile(
        "UPSTREAM_HTTP_ERROR",
        TaskState.failed,
        f"OpenCode rejected the request with HTTP status {status}",
    )


def extract_upstream_error_detail(response: httpx.Response | None) -> str | None:
    if response is None:
        return None

    payload = None
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value

    text = response.text.strip()
    if text:
        return text[:512]
    return None


__all__ = [
    "UpstreamHTTPErrorProfile",
    "extract_upstream_error_detail",
    "resolve_upstream_http_error_profile",
]

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx
from a2a.server.agent_execution import RequestContext
from a2a.types import Message, Part, TaskState

from ..opencode_client import UpstreamContractError

logger = logging.getLogger(__name__)

_INTERRUPT_ASKED_EVENT_TYPES = {"permission.asked", "question.asked"}
_INTERRUPT_RESOLVED_EVENT_TYPES = {"permission.replied", "question.replied", "question.rejected"}
_USAGE_PART_TYPES = {"step-finish"}
_SENSITIVE_LOG_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "password",
    "secret",
    "set-cookie",
    "token",
)


class BlockType(StrEnum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"


@dataclass(frozen=True)
class _NormalizedStreamChunk:
    part: Part
    content_key: str
    accumulate_content: bool
    append: bool
    block_type: BlockType
    internal_source: str
    shared_source: str
    message_id: str | None
    role: str | None


@dataclass(frozen=True)
class _PendingDelta:
    field: str
    delta: str
    message_id: str | None


@dataclass
class _StreamPartState:
    block_type: BlockType
    message_id: str | None
    role: str | None
    buffer: str = ""
    saw_delta: bool = False


def _merge_token_usage(
    base: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if base is None and incoming is None:
        return None
    merged: dict[str, Any] = dict(base) if base else {}
    if incoming:
        for key, value in incoming.items():
            if value is None:
                continue
            if key == "raw" and isinstance(value, Mapping):
                existing = merged.get("raw")
                if isinstance(existing, Mapping):
                    merged["raw"] = {**dict(existing), **dict(value)}
                else:
                    merged["raw"] = dict(value)
                continue
            merged[key] = value
    return merged or None


@dataclass
class _StreamOutputState:
    user_text: str
    stable_message_id: str
    event_id_namespace: str
    content_buffers: dict[BlockType, str] = field(default_factory=dict)
    progress_buffers: dict[str, str] = field(default_factory=dict)
    token_usage: dict[str, Any] | None = None
    upstream_error: _UpstreamInBandError | None = None
    pending_interrupt_request_ids: set[str] = field(default_factory=set)
    saw_any_chunk: bool = False
    emitted_stream_chunk: bool = False
    sequence: int = 0

    def should_drop_initial_user_echo(
        self,
        text: str,
        *,
        block_type: BlockType,
        role: str | None,
    ) -> bool:
        if role is not None:
            return False
        if block_type != BlockType.TEXT:
            return False
        if self.saw_any_chunk:
            return False
        user_text = self.user_text.strip()
        return bool(user_text) and text.strip() == user_text

    def register_chunk(
        self,
        *,
        block_type: BlockType,
        content_key: str,
        append: bool,
        accumulate_content: bool = True,
    ) -> tuple[bool, bool]:
        previous = self.content_buffers.get(block_type, "")
        next_value = f"{previous}{content_key}" if append and accumulate_content else content_key
        if next_value == previous:
            return False, False
        self.content_buffers[block_type] = next_value
        self.saw_any_chunk = True
        effective_append = self.emitted_stream_chunk
        self.emitted_stream_chunk = True
        return True, effective_append

    def register_progress(self, *, identity: str, content_key: str) -> bool:
        previous = self.progress_buffers.get(identity)
        if previous == content_key:
            return False
        self.progress_buffers[identity] = content_key
        return True

    def should_emit_final_snapshot(self, text: str) -> bool:
        if not text.strip():
            return False
        existing = self.content_buffers.get(BlockType.TEXT, "")
        if existing.strip() == text.strip():
            return False
        self.content_buffers[BlockType.TEXT] = text
        self.saw_any_chunk = True
        return True

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def resolve_message_id(self, message_id: str | None) -> str:
        if isinstance(message_id, str):
            normalized = message_id.strip()
            if normalized:
                return normalized
        return self.stable_message_id

    def build_event_id(self, sequence: int) -> str:
        return f"{self.event_id_namespace}:{sequence}"

    def ingest_token_usage(self, usage: Mapping[str, Any] | None) -> None:
        self.token_usage = _merge_token_usage(self.token_usage, usage)

    def mark_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            return False
        if normalized in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.add(normalized)
        return True

    def clear_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            return False
        if normalized not in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.discard(normalized)
        return True


@dataclass(frozen=True)
class _StreamTerminalSignal:
    state: TaskState
    error_type: str | None = None
    message: str | None = None
    upstream_status: int | None = None


@dataclass(frozen=True)
class _UpstreamErrorProfile:
    error_type: str
    state: TaskState
    default_message: str


@dataclass(frozen=True)
class _UpstreamInBandError:
    error_type: str
    state: TaskState
    message: str
    upstream_status: int | None = None


_UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS: dict[int, _UpstreamErrorProfile] = {
    400: _UpstreamErrorProfile(
        "UPSTREAM_BAD_REQUEST",
        TaskState.failed,
        "OpenCode rejected the request due to invalid input",
    ),
    401: _UpstreamErrorProfile(
        "UPSTREAM_UNAUTHORIZED",
        TaskState.auth_required,
        "OpenCode rejected the request due to authentication failure",
    ),
    403: _UpstreamErrorProfile(
        "UPSTREAM_PERMISSION_DENIED",
        TaskState.failed,
        "OpenCode rejected the request due to insufficient permissions",
    ),
    404: _UpstreamErrorProfile(
        "UPSTREAM_RESOURCE_NOT_FOUND",
        TaskState.failed,
        "OpenCode rejected the request because the target resource was not found",
    ),
    429: _UpstreamErrorProfile(
        "UPSTREAM_QUOTA_EXCEEDED",
        TaskState.failed,
        "OpenCode rejected the request due to quota limits",
    ),
}


def _resolve_upstream_error_profile(status: int) -> _UpstreamErrorProfile:
    if status in _UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS:
        return _UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS[status]
    if 400 <= status < 500:
        return _UpstreamErrorProfile(
            "UPSTREAM_CLIENT_ERROR",
            TaskState.failed,
            f"OpenCode rejected the request with client error {status}",
        )
    if status >= 500:
        return _UpstreamErrorProfile(
            "UPSTREAM_SERVER_ERROR",
            TaskState.failed,
            f"OpenCode rejected the request with server error {status}",
        )
    return _UpstreamErrorProfile(
        "UPSTREAM_HTTP_ERROR",
        TaskState.failed,
        f"OpenCode rejected the request with HTTP status {status}",
    )


def _extract_upstream_error_detail(response: httpx.Response | None) -> str | None:
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


def _format_upstream_error(
    exc: httpx.HTTPStatusError, *, request: str
) -> tuple[str, TaskState, str]:
    status = exc.response.status_code
    profile = _resolve_upstream_error_profile(status)
    detail = _extract_upstream_error_detail(exc.response)
    if detail:
        return (
            profile.error_type,
            profile.state,
            f"{profile.default_message} ({request}, status={status}, detail={detail}).",
        )
    return (
        profile.error_type,
        profile.state,
        f"{profile.default_message} ({request}, status={status}).",
    )


def _format_stream_terminal_error(
    *,
    detail: str | None,
    status: int | None,
    error_name: str | None,
) -> _StreamTerminalSignal:
    if status is not None:
        profile = _resolve_upstream_error_profile(status)
        if detail:
            message = (
                f"{profile.default_message} (session.error, status={status}, detail={detail})."
            )
        else:
            message = f"{profile.default_message} (session.error, status={status})."
        return _StreamTerminalSignal(
            state=profile.state,
            error_type=profile.error_type,
            message=message,
            upstream_status=status,
        )

    if error_name == "ProviderAuthError":
        if detail:
            message = (
                "OpenCode rejected the request due to authentication failure "
                f"(session.error, detail={detail})."
            )
        else:
            message = "OpenCode rejected the request due to authentication failure (session.error)."
        return _StreamTerminalSignal(
            state=TaskState.auth_required,
            error_type="UPSTREAM_UNAUTHORIZED",
            message=message,
        )

    if detail:
        message = f"OpenCode execution failed (session.error, detail={detail})."
    elif error_name:
        message = f"OpenCode execution failed (session.error, error={error_name})."
    else:
        message = "OpenCode execution failed (session.error)."
    return _StreamTerminalSignal(
        state=TaskState.failed,
        error_type="UPSTREAM_EXECUTION_ERROR",
        message=message,
    )


def _format_inband_upstream_error(
    *,
    source: str,
    detail: str | None,
    status: int | None,
    error_name: str | None,
) -> _UpstreamInBandError:
    if status is not None:
        profile = _resolve_upstream_error_profile(status)
        if detail:
            message = f"{profile.default_message} ({source}, status={status}, detail={detail})."
        else:
            message = f"{profile.default_message} ({source}, status={status})."
        return _UpstreamInBandError(
            error_type=profile.error_type,
            state=profile.state,
            message=message,
            upstream_status=status,
        )

    if error_name == "ProviderAuthError":
        if detail:
            message = (
                "OpenCode rejected the request due to authentication failure "
                f"({source}, detail={detail})."
            )
        else:
            message = f"OpenCode rejected the request due to authentication failure ({source})."
        return _UpstreamInBandError(
            error_type="UPSTREAM_UNAUTHORIZED",
            state=TaskState.auth_required,
            message=message,
        )

    if detail:
        message = f"OpenCode execution failed ({source}, detail={detail})."
    elif error_name:
        message = f"OpenCode execution failed ({source}, error={error_name})."
    else:
        message = f"OpenCode execution failed ({source})."
    return _UpstreamInBandError(
        error_type="UPSTREAM_EXECUTION_ERROR",
        state=TaskState.failed,
        message=message,
    )


async def _await_stream_terminal_signal(
    *,
    stream_task: asyncio.Task[None] | None,
    terminal_signal: asyncio.Future[_StreamTerminalSignal],
    session_id: str,
) -> _StreamTerminalSignal:
    if terminal_signal.done():
        return terminal_signal.result()
    if stream_task is None:
        raise RuntimeError("Streaming task was not initialized")

    terminal_wait_task = asyncio.create_task(_wait_for_terminal_signal(terminal_signal))
    try:
        done, _pending = await asyncio.wait(
            {stream_task, terminal_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if terminal_wait_task in done:
            return terminal_wait_task.result()
        if stream_task in done:
            with suppress(asyncio.CancelledError):
                await stream_task
            if terminal_signal.done():
                return terminal_signal.result()
            raise UpstreamContractError(
                "OpenCode event stream ended before terminal signal "
                f"(session_id={session_id}, expected session.idle or session.error)"
            )
        return await terminal_wait_task
    finally:
        if not terminal_wait_task.done():
            terminal_wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await terminal_wait_task


async def _wait_for_terminal_signal(
    terminal_signal: asyncio.Future[_StreamTerminalSignal],
) -> _StreamTerminalSignal:
    return await terminal_signal


class _TTLCache:
    """Bounded TTL cache for hashable key -> string value."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        maxsize: int,
        now: Callable[[], float] = time.monotonic,
        refresh_on_get: bool = False,
    ) -> None:
        self._ttl_seconds = int(ttl_seconds)
        self._maxsize = int(maxsize)
        self._now = now
        self._refresh_on_get = bool(refresh_on_get)
        self._store: dict[object, tuple[str, float]] = {}

    def get(self, key: object) -> str | None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        now = self._now()
        if expires_at <= now:
            self._store.pop(key, None)
            return None
        if self._refresh_on_get:
            self._store[key] = (value, now + float(self._ttl_seconds))
        return value

    def set(self, key: object, value: str) -> None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return
        now = self._now()
        expires_at = now + float(self._ttl_seconds)
        self._store[key] = (value, expires_at)
        self._evict_if_needed(now=now)

    def pop(self, key: object) -> None:
        self._store.pop(key, None)

    def _evict_if_needed(self, *, now: float) -> None:
        if len(self._store) <= self._maxsize:
            return
        expired = [key for key, (_, exp) in self._store.items() if exp <= now]
        for key in expired:
            self._store.pop(key, None)
        if len(self._store) <= self._maxsize:
            return
        overflow = len(self._store) - self._maxsize
        by_expiry = sorted(self._store.items(), key=lambda item: item[1][1])
        for key, _ in by_expiry[:overflow]:
            self._store.pop(key, None)


def _preview_log_value(value: Any, *, limit: int) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        rendered = str(value)
    if limit > 0 and len(rendered) > limit:
        return f"{rendered[:limit]}...[truncated]"
    return rendered


def _build_stream_artifact_metadata(
    *,
    block_type: BlockType,
    shared_source: str,
    message_id: str | None = None,
    role: str | None = None,
    event_id: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    stream_meta: dict[str, Any] = {
        "block_type": block_type.value,
        "source": shared_source,
    }
    if message_id:
        stream_meta["message_id"] = message_id
    if role:
        stream_meta["role"] = role
    if event_id:
        stream_meta["event_id"] = event_id
    if sequence is not None:
        stream_meta["sequence"] = sequence
    return {"shared": {"stream": stream_meta}}


def _build_output_metadata(
    *,
    session_id: str | None = None,
    session_title: str | None = None,
    usage: Mapping[str, Any] | None = None,
    stream: Mapping[str, Any] | None = None,
    progress: Mapping[str, Any] | None = None,
    interrupt: Mapping[str, Any] | None = None,
    opencode_private: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    shared_meta: dict[str, Any] = {}

    if session_id:
        session_meta: dict[str, Any] = {"id": session_id}
        if session_title is not None:
            session_meta["title"] = session_title
        shared_meta["session"] = session_meta
    if usage is not None:
        shared_meta["usage"] = dict(usage)
    if stream is not None:
        shared_meta["stream"] = dict(stream)
    if progress is not None:
        shared_meta["progress"] = dict(progress)
    if interrupt is not None:
        shared_meta["interrupt"] = dict(interrupt)
    if shared_meta:
        metadata["shared"] = shared_meta
    if opencode_private:
        metadata["opencode"] = dict(opencode_private)
    return metadata or None


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        if "." in normalized or "e" in normalized.lower():
            parsed = float(normalized)
            if parsed.is_integer():
                return int(parsed)
            return parsed
        return int(normalized)
    except ValueError:
        return None


def _extract_usage_from_info_like(info: Mapping[str, Any]) -> dict[str, Any] | None:
    tokens = info.get("tokens")
    if not isinstance(tokens, Mapping):
        return None

    usage: dict[str, Any] = {}

    input_tokens = _coerce_number(tokens.get("input"))
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens

    output_tokens = _coerce_number(tokens.get("output"))
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens

    total_tokens = _coerce_number(tokens.get("total"))
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    elif input_tokens is not None and output_tokens is not None:
        usage["total_tokens"] = input_tokens + output_tokens

    reasoning_tokens = _coerce_number(tokens.get("reasoning"))
    if reasoning_tokens is not None:
        usage["reasoning_tokens"] = reasoning_tokens

    cache = tokens.get("cache")
    if isinstance(cache, Mapping):
        cache_usage: dict[str, Any] = {}
        cache_read = _coerce_number(cache.get("read"))
        if cache_read is not None:
            cache_usage["read_tokens"] = cache_read
        cache_write = _coerce_number(cache.get("write"))
        if cache_write is not None:
            cache_usage["write_tokens"] = cache_write
        if cache_usage:
            usage["cache_tokens"] = cache_usage

    cost = _coerce_number(info.get("cost"))
    if cost is not None:
        usage["cost"] = cost

    if not usage:
        return None
    return usage


def _extract_token_usage(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = []
    info = payload.get("info")
    if isinstance(info, Mapping):
        candidates.append(info)

    props = payload.get("properties")
    if isinstance(props, Mapping):
        props_info = props.get("info")
        if isinstance(props_info, Mapping):
            candidates.append(props_info)
        part = props.get("part")
        if (
            isinstance(part, Mapping)
            and _extract_stream_part_type(part, props) in _USAGE_PART_TYPES
        ):
            candidates.append(part)

    for candidate in candidates:
        usage = _extract_usage_from_info_like(candidate)
        if usage is not None:
            return usage
    return None


def _normalize_role(role: Any) -> str | None:
    if not isinstance(role, str):
        return None
    value = role.strip().lower()
    if not value:
        return None
    if value == "assistant":
        return "agent"
    if value == "user":
        return "user"
    if value == "system":
        return "system"
    return None


def _extract_stream_role(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    role = part.get("role") or props.get("role")
    return _normalize_role(role)


def _extract_first_nonempty_string(
    source: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> str | None:
    if not isinstance(source, Mapping):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _extract_stream_session_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = _extract_first_nonempty_string(part, ("sessionID",))
    if candidate:
        return candidate
    return _extract_first_nonempty_string(props, ("sessionID",))


def _extract_event_session_id(event: Mapping[str, Any]) -> str | None:
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    direct = _extract_first_nonempty_string(props, ("sessionID",))
    if direct:
        return direct
    info = props.get("info")
    if isinstance(info, Mapping):
        info_session_id = _extract_first_nonempty_string(info, ("sessionID",))
        if info_session_id:
            return info_session_id
    part = props.get("part")
    if isinstance(part, Mapping):
        part_session_id = _extract_first_nonempty_string(part, ("sessionID",))
        if part_session_id:
            return part_session_id
    return None


def _extract_stream_terminal_signal(event: Mapping[str, Any]) -> _StreamTerminalSignal | None:
    event_type = event.get("type")
    if event_type == "session.idle":
        return _StreamTerminalSignal(state=TaskState.completed)
    if event_type != "session.error":
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return _StreamTerminalSignal(
            state=TaskState.failed,
            error_type="UPSTREAM_EXECUTION_ERROR",
            message="OpenCode execution failed (session.error).",
        )
    error = props.get("error")
    if not isinstance(error, Mapping):
        return _StreamTerminalSignal(
            state=TaskState.failed,
            error_type="UPSTREAM_EXECUTION_ERROR",
            message="OpenCode execution failed (session.error).",
        )
    error_name = _extract_first_nonempty_string(error, ("name",))
    error_data = error.get("data")
    error_data_map = error_data if isinstance(error_data, Mapping) else {}
    detail = _extract_first_nonempty_string(
        error_data_map, ("message",)
    ) or _extract_first_nonempty_string(error, ("message",))
    upstream_status = error_data_map.get("statusCode")
    if not isinstance(upstream_status, int):
        upstream_status = None
    return _format_stream_terminal_error(
        detail=detail,
        status=upstream_status,
        error_name=error_name,
    )


def _extract_upstream_error_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    source: str,
) -> _UpstreamInBandError | None:
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    error_name = _extract_first_nonempty_string(error, ("name",))
    error_data = error.get("data")
    error_data_map = error_data if isinstance(error_data, Mapping) else {}
    detail = _extract_first_nonempty_string(
        error_data_map, ("message",)
    ) or _extract_first_nonempty_string(error, ("message",))
    upstream_status = error_data_map.get("statusCode")
    if not isinstance(upstream_status, int):
        upstream_status = None
    return _format_inband_upstream_error(
        source=source,
        detail=detail,
        status=upstream_status,
        error_name=error_name,
    )


def _extract_upstream_error_from_response(
    response_raw: Mapping[str, Any] | None,
) -> _UpstreamInBandError | None:
    if not isinstance(response_raw, Mapping):
        return None
    info = response_raw.get("info")
    if not isinstance(info, Mapping):
        return None
    return _extract_upstream_error_from_payload(info, source="response.info.error")


def _extract_upstream_error_from_event(event: Mapping[str, Any]) -> _UpstreamInBandError | None:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    if event_type == "session.error":
        return _extract_upstream_error_from_payload(props, source="session.error")
    if event_type == "message.updated":
        info = props.get("info")
        if isinstance(info, Mapping):
            return _extract_upstream_error_from_payload(info, source="message.updated")
    return None


def _extract_progress_metadata(
    part: Mapping[str, Any],
    props: Mapping[str, Any],
) -> dict[str, Any] | None:
    part_type = _extract_stream_part_type(part, props)
    if part_type not in {"step-start", "step-finish", "snapshot"}:
        return None
    progress: dict[str, Any] = {"type": part_type}
    part_id = _extract_stream_part_id(part, props)
    if part_id:
        progress["part_id"] = part_id
    reason = _extract_first_nonempty_string(part, ("reason",))
    if reason:
        progress["reason"] = reason
    state = part.get("state")
    if isinstance(state, Mapping):
        status = _extract_first_nonempty_string(state, ("status",))
        if status:
            progress["status"] = status
        title = _extract_first_nonempty_string(state, ("title",))
        if title:
            progress["title"] = title
        subtitle = _extract_first_nonempty_string(state, ("subtitle",))
        if subtitle:
            progress["subtitle"] = subtitle
    return progress


def _build_progress_identity(part: Mapping[str, Any], props: Mapping[str, Any]) -> str:
    part_type = _extract_stream_part_type(part, props) or "unknown"
    part_id = _extract_stream_part_id(part, props)
    if part_id:
        return f"{part_type}:{part_id}"
    message_id = _extract_stream_message_id(part, props)
    if message_id:
        return f"{part_type}:{message_id}"
    return part_type


def _extract_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return result


def _extract_optional_string(source: Mapping[str, Any], key: str) -> str | None:
    value = source.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _normalize_interrupt_question_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        normalized_option: dict[str, str] = {}
        for key in ("label", "value", "description"):
            normalized = _extract_optional_string(item, key)
            if normalized is not None:
                normalized_option[key] = normalized
        if normalized_option:
            options.append(normalized_option)
    return options


def _normalize_interrupt_questions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    questions: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        normalized_question: dict[str, Any] = {}
        for key in ("header", "question"):
            normalized = _extract_optional_string(item, key)
            if normalized is not None:
                normalized_question[key] = normalized
        options = _normalize_interrupt_question_options(item.get("options"))
        if options:
            normalized_question["options"] = options
        if normalized_question:
            questions.append(normalized_question)
    return questions


def _extract_interrupt_asked_request_id(props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string(props, ("id",))


def _extract_interrupt_resolved_request_id(props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string(props, ("requestID",))


def _extract_interrupt_asked_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_ASKED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = _extract_interrupt_asked_request_id(props)
    if not request_id:
        return None
    if event_type == "permission.asked":
        details: dict[str, Any] = {
            "permission": _extract_optional_string(props, "permission"),
            "patterns": _extract_string_list(props.get("patterns")),
        }
        return {
            "request_id": request_id,
            "interrupt_type": "permission",
            "details": details,
        }
    details = {"questions": _normalize_interrupt_questions(props.get("questions"))}
    return {
        "request_id": request_id,
        "interrupt_type": "question",
        "details": details,
    }


def _extract_interrupt_resolved_event(event: Mapping[str, Any]) -> dict[str, str] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_RESOLVED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = _extract_interrupt_resolved_request_id(props)
    if not request_id:
        return None
    interrupt_type = "permission" if event_type.startswith("permission.") else "question"
    resolution = "rejected" if event_type == "question.rejected" else "replied"
    return {
        "request_id": request_id,
        "event_type": event_type,
        "interrupt_type": interrupt_type,
        "resolution": resolution,
    }


def _extract_stream_message_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = _extract_first_nonempty_string(part, ("messageID",))
    if candidate:
        return candidate
    return _extract_first_nonempty_string(props, ("messageID",))


def _extract_stream_part_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = _extract_first_nonempty_string(part, ("id",))
    if candidate:
        return candidate
    return _extract_first_nonempty_string(props, ("partID",))


def _extract_stream_part_type(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    del props
    value = part.get("type")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            return normalized
    return None


def _extract_stream_snapshot_text(part: Mapping[str, Any]) -> str | None:
    part_type = _extract_stream_part_type(part, {})
    if part_type in {"text", "reasoning"}:
        return _extract_first_nonempty_string(part, ("text",))
    return None


def _is_sensitive_log_field(key: str) -> bool:
    normalized = key.strip().lower().replace("_", "-")
    return any(marker in normalized for marker in _SENSITIVE_LOG_FIELD_MARKERS)


def _sanitize_log_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_log_field(key_text):
                sanitized[key_text] = "[redacted]"
                continue
            sanitized[key_text] = _sanitize_log_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_log_value(item) for item in value]
    return value


def _log_stream_event_debug(event: Mapping[str, Any], *, limit: int) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    event_type = event.get("type")
    props = event.get("properties")
    props_map = props if isinstance(props, Mapping) else {}
    part = props_map.get("part")
    part_map = part if isinstance(part, Mapping) else {}
    delta = props_map.get("delta")
    text = part_map.get("text")
    logger.debug(
        "OpenCode stream event type=%s session_id=%s message_id=%s part_id=%s part_type=%s "
        "delta_len=%s text_len=%s payload=%s",
        event_type if isinstance(event_type, str) else None,
        _extract_stream_session_id(part_map, props_map),
        _extract_stream_message_id(part_map, props_map),
        _extract_stream_part_id(part_map, props_map),
        _extract_stream_part_type(part_map, props_map),
        len(delta) if isinstance(delta, str) else None,
        len(text) if isinstance(text, str) else None,
        _preview_log_value(_sanitize_log_value(event), limit=limit),
    )


def _map_part_type_to_block_type(part_type: str | None) -> BlockType | None:
    if not part_type:
        return None
    if part_type == "text":
        return BlockType.TEXT
    if part_type == "reasoning":
        return BlockType.REASONING
    if part_type == "tool":
        return BlockType.TOOL_CALL
    return None


def _resolve_stream_block_type(
    part: Mapping[str, Any], props: Mapping[str, Any]
) -> BlockType | None:
    return _map_part_type_to_block_type(_extract_stream_part_type(part, props))


def _extract_tool_part_payload(part: Mapping[str, Any]) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    for source_key in ("callID",):
        value = part.get(source_key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                payload["call_id"] = normalized
                break
    for source_key in ("tool",):
        value = part.get(source_key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                payload["tool"] = normalized
                break
    state = part.get("state")
    if isinstance(state, Mapping):
        status = state.get("status")
        if isinstance(status, str):
            normalized = status.strip()
            if normalized:
                payload["status"] = normalized
        for key in ("title", "subtitle", "input", "output", "error"):
            value = state.get(key)
            if value is not None:
                payload[key] = value
    if not payload:
        return None
    return payload


def _build_history(context: RequestContext) -> list[Message]:
    if context.current_task and context.current_task.history:
        history = list(context.current_task.history)
    else:
        history = []
        if context.message:
            history.append(context.message)
    return history


def _iter_metadata_maps(context: RequestContext, namespace: str):
    try:
        meta = context.metadata
    except Exception:
        meta = None

    if isinstance(meta, Mapping):
        namespaced_meta = meta.get(namespace)
        if isinstance(namespaced_meta, Mapping):
            yield namespaced_meta

    if context.message is not None:
        msg_meta = getattr(context.message, "metadata", None) or {}
        if isinstance(msg_meta, Mapping):
            namespaced_meta = msg_meta.get(namespace)
            if isinstance(namespaced_meta, Mapping):
                yield namespaced_meta


def _extract_namespaced_string_metadata(
    context: RequestContext,
    *,
    namespace: str,
    path: tuple[str, ...],
) -> str | None:
    for namespaced_meta in _iter_metadata_maps(context, namespace):
        current: Any = namespaced_meta
        for part in path[:-1]:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(part)
        if not isinstance(current, Mapping):
            continue
        candidate = current.get(path[-1])
        if isinstance(candidate, str):
            value = candidate.strip()
            if value:
                return value
    return None


def _extract_shared_session_id(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace="shared",
        path=("session", "id"),
    )


def _extract_shared_model(context: RequestContext) -> dict[str, str] | None:
    provider_id = _extract_namespaced_string_metadata(
        context,
        namespace="shared",
        path=("model", "providerID"),
    )
    model_id = _extract_namespaced_string_metadata(
        context,
        namespace="shared",
        path=("model", "modelID"),
    )
    if provider_id is None or model_id is None:
        return None
    return {"providerID": provider_id, "modelID": model_id}


def _extract_opencode_directory(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace="opencode",
        path=("directory",),
    )

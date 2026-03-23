from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from a2a.types import TaskState

from .stream_state import BlockType
from .upstream_error_translator import (
    _format_inband_upstream_error,
    _format_stream_terminal_error,
    _StreamTerminalSignal,
    _UpstreamInBandError,
)

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


def _preview_log_value(value: Any, *, limit: int) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        rendered = str(value)
    if limit > 0 and len(rendered) > limit:
        return f"{rendered[:limit]}...[truncated]"
    return rendered


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

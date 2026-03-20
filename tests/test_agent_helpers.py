from __future__ import annotations

import asyncio

import httpx
import pytest
from a2a.types import TaskState

from opencode_a2a_server.execution.executor import (
    BlockType,
    _await_stream_terminal_signal,
    _build_output_metadata,
    _build_progress_identity,
    _coerce_number,
    _extract_event_session_id,
    _extract_interrupt_asked_event,
    _extract_interrupt_resolved_event,
    _extract_progress_metadata,
    _extract_stream_session_id,
    _extract_stream_snapshot_text,
    _extract_stream_terminal_signal,
    _extract_token_usage,
    _extract_upstream_error_detail,
    _extract_upstream_error_from_event,
    _extract_upstream_error_from_response,
    _format_inband_upstream_error,
    _format_stream_terminal_error,
    _format_upstream_error,
    _merge_token_usage,
    _normalize_interrupt_question_options,
    _normalize_interrupt_questions,
    _normalize_role,
    _preview_log_value,
    _resolve_upstream_error_profile,
    _StreamOutputState,
    _TTLCache,
)
from opencode_a2a_server.opencode_client import UpstreamContractError


def test_stream_output_state_covers_echo_progress_and_interrupt_edges() -> None:
    state = _StreamOutputState(
        user_text=" hello ",
        stable_message_id="msg-stable",
        event_id_namespace="evt-ns",
    )

    assert (
        state.should_drop_initial_user_echo("hello", block_type=BlockType.TEXT, role=None) is True
    )
    assert (
        state.should_drop_initial_user_echo("hello", block_type=BlockType.REASONING, role=None)
        is False
    )
    assert (
        state.should_drop_initial_user_echo("hello", block_type=BlockType.TEXT, role="user")
        is False
    )

    changed, append = state.register_chunk(
        block_type=BlockType.TEXT,
        content_key="draft",
        append=False,
    )
    assert (changed, append) == (True, False)
    assert state.register_chunk(
        block_type=BlockType.TEXT,
        content_key="draft",
        append=False,
    ) == (False, False)
    assert state.register_progress(identity="step:1", content_key="running") is True
    assert state.register_progress(identity="step:1", content_key="running") is False
    assert state.should_emit_final_snapshot(" ") is False
    assert state.should_emit_final_snapshot("draft") is False
    assert state.should_emit_final_snapshot("final answer") is True
    assert state.resolve_message_id("  msg-1  ") == "msg-1"
    assert state.resolve_message_id("   ") == "msg-stable"
    assert state.build_event_id(state.next_sequence()) == "evt-ns:1"
    assert state.mark_interrupt_pending(" ") is False
    assert state.mark_interrupt_pending("req-1") is True
    assert state.mark_interrupt_pending("req-1") is False
    assert state.clear_interrupt_pending(" ") is False
    assert state.clear_interrupt_pending("req-missing") is False
    assert state.clear_interrupt_pending("req-1") is True


def test_resolve_upstream_error_profile_covers_known_and_fallback_statuses() -> None:
    assert _resolve_upstream_error_profile(401).error_type == "UPSTREAM_UNAUTHORIZED"
    assert _resolve_upstream_error_profile(418).error_type == "UPSTREAM_CLIENT_ERROR"
    assert _resolve_upstream_error_profile(503).error_type == "UPSTREAM_SERVER_ERROR"
    assert _resolve_upstream_error_profile(302).error_type == "UPSTREAM_HTTP_ERROR"


def test_extract_upstream_error_detail_prefers_json_fields_then_text() -> None:
    request = httpx.Request("GET", "http://localhost/test")

    response_with_json = httpx.Response(
        400,
        request=request,
        json={"error": " invalid model "},
    )
    assert _extract_upstream_error_detail(response_with_json) == "invalid model"

    response_with_text = httpx.Response(500, request=request, text="x" * 600)
    assert _extract_upstream_error_detail(response_with_text) == "x" * 512
    assert _extract_upstream_error_detail(None) is None


def test_format_upstream_errors_include_detail_and_provider_auth_paths() -> None:
    request = httpx.Request("POST", "http://localhost/session")
    response = httpx.Response(429, request=request, json={"detail": "rate limit"})
    exc = httpx.HTTPStatusError("quota", request=request, response=response)

    error_type, state, message = _format_upstream_error(exc, request="send message")
    assert error_type == "UPSTREAM_QUOTA_EXCEEDED"
    assert state == TaskState.failed
    assert "detail=rate limit" in message

    terminal = _format_stream_terminal_error(
        detail="login required",
        status=None,
        error_name="ProviderAuthError",
    )
    assert terminal.state == TaskState.auth_required
    assert terminal.error_type == "UPSTREAM_UNAUTHORIZED"

    inband = _format_inband_upstream_error(
        source="message.updated",
        detail=None,
        status=None,
        error_name="SomeOtherError",
    )
    assert inband.state == TaskState.failed
    assert inband.error_type == "UPSTREAM_EXECUTION_ERROR"
    assert "error=SomeOtherError" in inband.message


@pytest.mark.asyncio
async def test_await_stream_terminal_signal_handles_shortcuts_and_missing_signal() -> None:
    loop = asyncio.get_running_loop()

    ready = loop.create_future()
    ready.set_result(_format_stream_terminal_error(detail=None, status=401, error_name=None))
    result = await _await_stream_terminal_signal(
        stream_task=None,
        terminal_signal=ready,
        session_id="ses-1",
    )
    assert result.error_type == "UPSTREAM_UNAUTHORIZED"

    pending = loop.create_future()
    with pytest.raises(RuntimeError, match="Streaming task was not initialized"):
        await _await_stream_terminal_signal(
            stream_task=None,
            terminal_signal=pending,
            session_id="ses-1",
        )

    async def finish_without_terminal() -> None:
        return None

    with pytest.raises(UpstreamContractError, match="event stream ended before terminal signal"):
        await _await_stream_terminal_signal(
            stream_task=asyncio.create_task(finish_without_terminal()),
            terminal_signal=loop.create_future(),
            session_id="ses-2",
        )


def test_ttl_cache_handles_disabled_refresh_and_pop() -> None:
    now = 100.0
    cache = _TTLCache(ttl_seconds=10, maxsize=2, now=lambda: now, refresh_on_get=True)

    cache.set("a", "1")
    assert cache.get("a") == "1"

    now = 105.0
    assert cache.get("a") == "1"

    now = 114.0
    assert cache.get("a") == "1"

    cache.set("b", "2")
    cache.set("c", "3")
    assert cache.get("b") == "2"
    cache.pop("b")
    assert cache.get("b") is None

    disabled = _TTLCache(ttl_seconds=0, maxsize=1)
    disabled.set("x", "1")
    assert disabled.get("x") is None


def test_build_output_metadata_and_number_coercion_helpers() -> None:
    metadata = _build_output_metadata(
        session_id="ses-1",
        session_title=None,
        usage={"total_tokens": 3},
        stream={"block_type": "text"},
        progress={"type": "step-finish"},
        interrupt={"phase": "asked"},
        opencode_private={"directory": "/tmp/workspace"},
    )

    assert metadata == {
        "shared": {
            "session": {"id": "ses-1"},
            "usage": {"total_tokens": 3},
            "stream": {"block_type": "text"},
            "progress": {"type": "step-finish"},
            "interrupt": {"phase": "asked"},
        },
        "opencode": {"directory": "/tmp/workspace"},
    }
    assert _build_output_metadata() is None
    assert _coerce_number(True) is None
    assert _coerce_number(3.0) == 3
    assert _coerce_number("1.5") == 1.5
    assert _coerce_number("1e2") == 100
    assert _coerce_number("bad") is None


def test_extract_token_usage_and_merge_helpers_cover_fallbacks() -> None:
    payload = {
        "properties": {
            "part": {
                "type": "step-finish",
                "tokens": {
                    "input": "2",
                    "output": 3,
                    "reasoning": "4",
                    "cache": {"read": "5", "write": 6},
                },
            }
        }
    }

    assert _extract_token_usage(payload) == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
        "reasoning_tokens": 4,
        "cache_tokens": {"read_tokens": 5, "write_tokens": 6},
    }
    assert _extract_token_usage({"info": {"cost": "1.25", "tokens": {}}}) == {"cost": 1.25}
    assert _extract_token_usage("bad") is None
    assert _merge_token_usage(None, None) is None
    assert _merge_token_usage({"raw": {"a": 1}, "total_tokens": 2}, {"raw": {"b": 2}}) == {
        "raw": {"a": 1, "b": 2},
        "total_tokens": 2,
    }


def test_role_session_and_terminal_extractors_cover_event_shapes() -> None:
    assert _normalize_role(" assistant ") == "agent"
    assert _normalize_role("SYSTEM") == "system"
    assert _normalize_role("tool") is None

    part = {"sessionID": "ses-part", "text": "hello", "type": "text"}
    props = {"sessionID": "ses-props", "info": {"sessionID": "ses-info"}}
    assert _extract_stream_session_id(part, props) == "ses-part"
    assert _extract_event_session_id({"properties": props}) == "ses-props"
    assert (
        _extract_event_session_id({"properties": {"info": {"sessionID": "ses-info"}}}) == "ses-info"
    )
    assert _extract_stream_snapshot_text(part) == "hello"
    assert _extract_stream_snapshot_text({"type": "tool", "text": "ignored"}) is None

    idle = _extract_stream_terminal_signal({"type": "session.idle"})
    assert idle is not None
    assert idle.state == TaskState.completed

    terminal = _extract_stream_terminal_signal(
        {
            "type": "session.error",
            "properties": {
                "error": {
                    "name": "ProviderAuthError",
                    "data": {"message": "auth failed", "statusCode": 401},
                }
            },
        }
    )
    assert terminal is not None
    assert terminal.state == TaskState.auth_required
    assert terminal.upstream_status == 401


def test_upstream_error_and_progress_extractors_cover_edge_cases() -> None:
    response_error = _extract_upstream_error_from_response(
        {"info": {"error": {"name": "APIError", "message": "bad output"}}}
    )
    assert response_error is not None
    assert response_error.error_type == "UPSTREAM_EXECUTION_ERROR"

    event_error = _extract_upstream_error_from_event(
        {
            "type": "session.error",
            "properties": {"error": {"data": {"message": "denied", "statusCode": 403}}},
        }
    )
    assert event_error is not None
    assert event_error.error_type == "UPSTREAM_PERMISSION_DENIED"

    progress = _extract_progress_metadata(
        {
            "type": "step-finish",
            "id": "part-1",
            "reason": "done",
            "state": {"status": "ok", "title": "Write tests", "subtitle": "finished"},
        },
        {"messageID": "msg-1"},
    )
    assert progress == {
        "type": "step-finish",
        "part_id": "part-1",
        "reason": "done",
        "status": "ok",
        "title": "Write tests",
        "subtitle": "finished",
    }
    assert (
        _build_progress_identity(
            {"type": "snapshot"},
            {"messageID": "msg-1"},
        )
        == "snapshot:msg-1"
    )


def test_interrupt_question_and_event_extractors_normalize_payloads() -> None:
    options = _normalize_interrupt_question_options(
        [
            {"label": " Yes ", "value": " y ", "description": " proceed "},
            {"label": "   "},
            "skip",
        ]
    )
    assert options == [{"label": "Yes", "value": "y", "description": "proceed"}]

    questions = _normalize_interrupt_questions(
        [
            {
                "header": "Confirm",
                "question": "Proceed?",
                "options": [{"label": "Yes", "value": "yes"}],
            },
            {"question": "  "},
        ]
    )
    assert questions == [
        {
            "header": "Confirm",
            "question": "Proceed?",
            "options": [{"label": "Yes", "value": "yes"}],
        }
    ]

    asked = _extract_interrupt_asked_event(
        {
            "type": "permission.asked",
            "properties": {"id": "perm-1", "permission": "bash", "patterns": [" /tmp/* ", 1]},
        }
    )
    assert asked == {
        "request_id": "perm-1",
        "interrupt_type": "permission",
        "details": {"permission": "bash", "patterns": ["/tmp/*"]},
    }

    asked_question = _extract_interrupt_asked_event(
        {
            "type": "question.asked",
            "properties": {
                "id": "q-1",
                "questions": [{"header": "Need input", "question": "Pick one"}],
            },
        }
    )
    assert asked_question is not None
    assert asked_question["interrupt_type"] == "question"

    resolved = _extract_interrupt_resolved_event(
        {"type": "question.rejected", "properties": {"requestID": "q-1"}}
    )
    assert resolved == {
        "request_id": "q-1",
        "event_type": "question.rejected",
        "interrupt_type": "question",
        "resolution": "rejected",
    }


def test_preview_log_value_truncates_and_falls_back_to_str() -> None:
    class _BrokenJson:
        def __str__(self) -> str:
            return "broken"

    preview = _preview_log_value({"token": "x" * 20}, limit=10)
    assert preview.endswith("...[truncated]")
    assert _preview_log_value(_BrokenJson(), limit=100) == "broken"

from __future__ import annotations

from a2a.types import InvalidParamsError

from opencode_a2a.jsonrpc.error_responses import (
    interrupt_not_found_error,
    interrupt_type_mismatch_error,
    invalid_params_error,
    method_not_supported_error,
    session_forbidden_error,
    session_not_found_error,
    upstream_http_error,
    upstream_payload_error,
    upstream_unreachable_error,
)


def test_jsonrpc_error_mapping_helpers_preserve_business_contract_fields() -> None:
    unsupported = method_not_supported_error(
        method="unsupported.method",
        supported_methods=["message/send", "tasks/get"],
        protocol_version="0.3.0",
    )
    assert unsupported.code == -32601
    assert unsupported.data["type"] == "METHOD_NOT_SUPPORTED"

    forbidden = session_forbidden_error(-32006, session_id="s-1")
    assert forbidden.code == -32006
    assert forbidden.data == {"type": "SESSION_FORBIDDEN", "session_id": "s-1"}

    missing_session = session_not_found_error(-32001, session_id="s-404")
    assert missing_session.data == {"type": "SESSION_NOT_FOUND", "session_id": "s-404"}

    expired_interrupt = interrupt_not_found_error(-32007, request_id="req-1", expired=True)
    assert expired_interrupt.data == {
        "type": "INTERRUPT_REQUEST_EXPIRED",
        "request_id": "req-1",
    }

    mismatch_interrupt = interrupt_type_mismatch_error(
        -32008,
        request_id="req-2",
        expected_interrupt_type="permission",
        actual_interrupt_type="question",
    )
    assert mismatch_interrupt.data == {
        "type": "INTERRUPT_TYPE_MISMATCH",
        "request_id": "req-2",
        "expected_interrupt_type": "permission",
        "actual_interrupt_type": "question",
    }


def test_jsonrpc_error_mapping_helpers_build_upstream_envelopes() -> None:
    backpressure_detail = (
        "OpenCode upstream request concurrency limit exceeded while calling /session (limit=1)"
    )
    http_error = upstream_http_error(
        -32003,
        upstream_status=503,
        method="opencode.sessions.command",
        session_id="s-1",
    )
    assert http_error.data == {
        "type": "UPSTREAM_HTTP_ERROR",
        "upstream_status": 503,
        "method": "opencode.sessions.command",
        "session_id": "s-1",
    }

    unreachable = upstream_unreachable_error(
        -32002,
        request_id="req-1",
        detail=backpressure_detail,
    )
    assert unreachable.data == {
        "type": "UPSTREAM_UNREACHABLE",
        "request_id": "req-1",
        "detail": backpressure_detail,
    }

    payload_error = upstream_payload_error(
        -32005,
        detail="payload mismatch",
        method="opencode.providers.list",
    )
    assert payload_error.data == {
        "type": "UPSTREAM_PAYLOAD_ERROR",
        "detail": "payload mismatch",
        "method": "opencode.providers.list",
    }


def test_invalid_error_helper_wraps_a2a_error() -> None:
    invalid = invalid_params_error(
        "bad field",
        data={"type": "INVALID_FIELD", "field": "request"},
    )
    assert isinstance(invalid.root, InvalidParamsError)
    assert invalid.root.message == "bad field"
    assert invalid.root.data == {"type": "INVALID_FIELD", "field": "request"}

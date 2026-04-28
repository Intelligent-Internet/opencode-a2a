from __future__ import annotations

from a2a.types import InvalidParamsError, UnsupportedOperationError

from opencode_a2a.jsonrpc.error_responses import (
    GOOGLE_RPC_ERROR_INFO_TYPE,
    adapt_jsonrpc_error,
    authorization_forbidden_error,
    interrupt_not_found_error,
    interrupt_type_mismatch_error,
    invalid_params_error,
    method_not_supported_error,
    session_forbidden_error,
    session_not_found_error,
    upstream_http_error,
    upstream_payload_error,
    upstream_unreachable_error,
    version_not_supported_error,
)


def test_jsonrpc_error_mapping_helpers_preserve_business_contract_fields() -> None:
    unsupported = method_not_supported_error(
        method="unsupported.method",
        supported_methods=["SendMessage", "GetTask"],
        protocol_version="1.0",
    )
    assert unsupported.code == -32601
    assert unsupported.data["type"] == "METHOD_NOT_SUPPORTED"

    forbidden = session_forbidden_error(-32006, session_id="s-1")
    assert forbidden.code == -32006
    assert forbidden.data == {"type": "SESSION_FORBIDDEN", "session_id": "s-1"}

    authz_forbidden = authorization_forbidden_error(
        -32007,
        method="opencode.sessions.shell",
        capability="session_shell",
        credential_id="ops-basic",
    )
    assert authz_forbidden.code == -32007
    assert authz_forbidden.data == {
        "type": "AUTHORIZATION_FORBIDDEN",
        "method": "opencode.sessions.shell",
        "capability": "session_shell",
        "credential_id": "ops-basic",
    }

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
    assert isinstance(invalid, InvalidParamsError)
    assert invalid.message == "bad field"
    assert invalid.data == {"type": "INVALID_FIELD", "field": "request"}


def test_version_not_supported_error_includes_supported_versions() -> None:
    error = version_not_supported_error(
        requested_version="2.0",
        supported_protocol_versions=["1.0"],
        default_protocol_version="1.0",
    )

    assert error.code == -32001
    assert error.message == "Unsupported A2A version: 2.0"
    assert error.data == {
        "type": "VERSION_NOT_SUPPORTED",
        "requested_version": "2.0",
        "supported_protocol_versions": ["1.0"],
        "default_protocol_version": "1.0",
    }


def test_adapt_standard_jsonrpc_error_for_v1_uses_standard_message_and_camel_case_data() -> None:
    adapted = adapt_jsonrpc_error(
        method_not_supported_error(
            method="unsupported.method",
            supported_methods=["SendMessage", "GetTask"],
            protocol_version="1.0",
        ),
    )

    assert adapted.message == "Method not found"
    assert adapted.data == {
        "method": "unsupported.method",
        "supportedMethods": ["SendMessage", "GetTask"],
        "protocolVersion": "1.0",
    }


def test_adapt_a2a_specific_error_for_v1_uses_error_info_details() -> None:
    adapted = adapt_jsonrpc_error(
        version_not_supported_error(
            requested_version="1.1",
            supported_protocol_versions=["1.0"],
            default_protocol_version="1.0",
        ),
    )

    assert adapted.code == -32001
    assert adapted.data[0] == {
        "@type": GOOGLE_RPC_ERROR_INFO_TYPE,
        "reason": "VERSION_NOT_SUPPORTED",
        "domain": "a2a-protocol.org",
        "metadata": {
            "requestedVersion": "1.1",
            "supportedProtocolVersions": '["1.0"]',
            "defaultProtocolVersion": "1.0",
        },
    }
    assert adapted.data[1] == {
        "@type": "type.googleapis.com/opencode_a2a.ErrorContext",
        "requestedVersion": "1.1",
        "supportedProtocolVersions": ["1.0"],
        "defaultProtocolVersion": "1.0",
    }


def test_adapt_a2a_root_error_for_v1_uses_error_type_reason() -> None:
    adapted = adapt_jsonrpc_error(UnsupportedOperationError())

    assert adapted.code == -32004
    assert adapted.message == "This operation is not supported"
    assert adapted.data == [
        {
            "@type": GOOGLE_RPC_ERROR_INFO_TYPE,
            "reason": "UNSUPPORTED_OPERATION",
            "domain": "a2a-protocol.org",
        }
    ]

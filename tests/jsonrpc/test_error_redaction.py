from __future__ import annotations

import json

from a2a.types import InvalidParamsError

from opencode_a2a.jsonrpc.error_responses import adapt_jsonrpc_error, build_http_error_body
from opencode_a2a.jsonrpc.models import JSONRPCError
from opencode_a2a.redact import REDACTED_PATH_PLACEHOLDER
from opencode_a2a.server.application import create_app
from tests.support.settings import make_settings


def test_adapt_jsonrpc_error_redacts_message_and_metadata() -> None:
    error = JSONRPCError(
        code=-32001,
        message="Session file '/home/ubuntu/sessions/s1.json' missing",
        data={
            "type": "SESSION_NOT_FOUND",
            "path": "/home/ubuntu/sessions/s1.json",
            "nested": {"location": r"C:\Users\alice\x"},
        },
    )

    adapted = adapt_jsonrpc_error(error)

    assert adapted.code == -32001
    assert REDACTED_PATH_PLACEHOLDER in adapted.message
    assert "/home/ubuntu/sessions/s1.json" not in adapted.message
    dumped = json.dumps(adapted.data)
    assert REDACTED_PATH_PLACEHOLDER in dumped
    assert "/home/ubuntu/sessions/s1.json" not in dumped
    assert r"C:\Users\alice\x" not in dumped


def test_adapt_jsonrpc_error_redacts_data_for_standard_codes() -> None:
    error = InvalidParamsError(
        message="Invalid params",
        data={"field": "directory", "value": "/home/ubuntu/project"},
    )

    adapted = adapt_jsonrpc_error(error)

    assert adapted.code == -32602
    dumped = json.dumps(adapted.data)
    assert REDACTED_PATH_PLACEHOLDER in dumped
    assert "/home/ubuntu/project" not in dumped


def test_build_http_error_body_redacts_message_and_metadata() -> None:
    body = build_http_error_body(
        status_code=500,
        status="INTERNAL",
        message="Unhandled path /opt/opencode/bin/tool",
        metadata={"directory": "/opt/opencode/bin"},
    )

    payload = body["error"]
    assert payload["message"] == f"Unhandled path {REDACTED_PATH_PLACEHOLDER}"
    dumped = json.dumps(payload["details"])
    assert REDACTED_PATH_PLACEHOLDER in dumped
    assert "/opt/opencode/bin" not in dumped


def test_generate_error_response_redacts_raw_exception_text() -> None:
    app = create_app(make_settings())
    jsonrpc_app = app.state._jsonrpc_app

    response = jsonrpc_app._generate_error_response("1", ValueError("broken at /home/ubuntu/x"))

    body = response.body.decode("utf-8")
    assert REDACTED_PATH_PLACEHOLDER in body
    assert "/home/ubuntu/x" not in body

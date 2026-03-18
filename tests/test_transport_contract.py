import logging

import httpx
import pytest
from a2a.types import TransportProtocol

from opencode_a2a_server.app import _normalize_log_level, build_agent_card, create_app
from tests.helpers import DummyChatOpencodeClient, make_settings


def test_agent_card_declares_dual_stack_with_http_json_preferred() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    assert card.preferred_transport == TransportProtocol.http_json
    transports = {iface.transport for iface in card.additional_interfaces or []}
    assert TransportProtocol.http_json in transports
    assert TransportProtocol.jsonrpc in transports


def test_normalize_log_level_falls_back_to_warning_for_invalid_value() -> None:
    assert _normalize_log_level("warn") == "WARNING"


def test_rest_subscription_route_matches_current_sdk_contract() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    route_paths = {route.path for route in app.router.routes if hasattr(route, "path")}

    assert "/v1/tasks/{id}:subscribe" in route_paths
    assert "/v1/tasks/{id}:resubscribe" not in route_paths


def test_openapi_rest_message_routes_include_schema_and_examples() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    openapi = app.openapi()
    paths = openapi["paths"]

    expected: dict[str, str] = {
        "/v1/message:send": "#/components/schemas/SendMessageRequest",
        "/v1/message:stream": "#/components/schemas/SendStreamingMessageRequest",
    }
    for path, expected_schema_ref in expected.items():
        post = paths[path]["post"]
        assert post["summary"] in {"Send Message (HTTP+JSON)", "Stream Message (HTTP+JSON)"}
        content = post.get("requestBody", {}).get("content", {}).get("application/json", {})
        assert content.get("schema", {}).get("$ref") == expected_schema_ref
        examples = content.get("examples")
        assert isinstance(examples, dict)
        assert "basic_message" in examples
        assert "continue_session" in examples
        assert "message_with_file_input" in examples


def test_openapi_jsonrpc_examples_include_core_message_methods() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    openapi = app.openapi()
    post = openapi["paths"]["/"]["post"]
    examples = (
        post.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("examples", {})
    )
    example_values = examples.values()
    methods = {value.get("value", {}).get("method") for value in example_values}
    assert "message/send" in methods
    assert "message/stream" in methods
    assert "message_send_file_input" in examples


@pytest.mark.asyncio
async def test_dual_stack_send_accepts_transport_native_payloads(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    rest_payload = {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "content": [{"text": "hello from rest"}],
        }
    }
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rpc",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello from jsonrpc"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_resp = await client.post("/v1/message:send", headers=headers, json=rest_payload)
        assert rest_resp.status_code == 200

        rpc_resp = await client.post("/", headers=headers, json=rpc_payload)
        assert rpc_resp.status_code == 200
        assert rpc_resp.json().get("error") is None


@pytest.mark.asyncio
async def test_dual_stack_send_rejects_cross_transport_payload_shapes(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    rest_with_jsonrpc_shape = {
        "message": {
            "messageId": "m-rest-cross",
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
        }
    }
    full_jsonrpc_envelope = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rest-cross-envelope",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello from envelope"}],
            }
        },
    }
    rpc_with_rest_shape = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rpc-cross",
                "role": "ROLE_USER",
                "content": [{"text": "hello"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_resp = await client.post(
            "/v1/message:send",
            headers=headers,
            json=rest_with_jsonrpc_shape,
        )
        assert rest_resp.status_code == 400
        assert "Invalid HTTP+JSON payload" in rest_resp.text

        rest_envelope_resp = await client.post(
            "/v1/message:send",
            headers=headers,
            json=full_jsonrpc_envelope,
        )
        assert rest_envelope_resp.status_code == 400
        assert "Invalid HTTP+JSON payload" in rest_envelope_resp.text

        rpc_resp = await client.post("/", headers=headers, json=rpc_with_rest_shape)
        assert rpc_resp.status_code == 200
        payload = rpc_resp.json()
        assert payload["error"]["code"] == -32602


def _rest_message_payload() -> dict:
    return {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "content": [{"text": "hello from rest"}],
        }
    }


def _jsonrpc_message_send_payload(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "message/send",
        "params": {
            "message": {
                "messageId": "m-rpc",
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


@pytest.mark.asyncio
async def test_log_payloads_keeps_body_for_rest_handler(monkeypatch, caplog) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/message:send",
                headers=headers,
                json=_rest_message_payload(),
            )

            assert resp.status_code == 200

    assert any(
        "A2A request POST /v1/message:send body=" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_payloads_streaming_response_path(monkeypatch, caplog) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST", "/v1/message:stream", headers=headers, json=_rest_message_payload()
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        break

    assert any(
        "A2A response /v1/message:stream status=200" in record.message
        or "A2A response /v1/message:stream streaming" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_payloads_omits_non_json_request_body(monkeypatch, caplog) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/octet-stream",
    }

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/", headers=headers, content=b"\x00\x01\x02\x03")
            assert resp.status_code < 500

    assert any(
        "body=[omitted non-json content-type=application/octet-stream]" in record.message
        for record in caplog.records
    )
    assert "\\x00\\x01\\x02\\x03" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_text_plain_request_body(monkeypatch, caplog) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", a2a_log_payloads=True))
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "text/plain",
    }
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":'
        '{"messageId":"m","role":"user","parts":[{"kind":"text","text":"secret"}]}}}'
    )

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/", headers=headers, content=body)
            assert resp.status_code < 500

    assert any(
        "body=[omitted non-json content-type=text/plain]" in record.message
        for record in caplog.records
    )
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_when_content_length_missing(monkeypatch, caplog) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_log_payloads=True,
            a2a_log_body_limit=64,
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":'
        b'{"messageId":"m","role":"user","parts":[{"kind":"text","text":"missing-cl"}]}}}'
    )

    async def _body_stream():
        yield body

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/",
                headers=headers,
                content=_body_stream(),
            )
            assert resp.status_code == 200

    assert any(
        "body=[omitted missing content-length with limit=64]" in record.message
        for record in caplog.records
    )
    assert any(
        "body=[omitted request_missing content-length with limit=64]" in record.message
        for record in caplog.records
    )
    assert "missing-cl" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_oversized_request_body(monkeypatch, caplog) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_log_payloads=True,
            a2a_log_body_limit=64,
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    oversized_text = "x" * 512

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a_server.app"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/",
                headers=headers,
                json=_jsonrpc_message_send_payload(oversized_text),
            )
            assert resp.status_code == 200

    assert any(
        "body=[omitted content-length=" in record.message and "exceeds limit=64" in record.message
        for record in caplog.records
    )
    assert oversized_text not in caplog.text


@pytest.mark.asyncio
async def test_request_body_limit_rejects_oversized_content_length(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="test-token", a2a_max_request_body_bytes=64)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            headers=headers,
            json=_jsonrpc_message_send_payload("x" * 512),
        )

    assert resp.status_code == 413
    assert resp.json() == {"error": "Request body too large", "max_bytes": 64}


@pytest.mark.asyncio
async def test_request_body_limit_rejects_oversized_stream_without_content_length(
    monkeypatch,
) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="test-token", a2a_max_request_body_bytes=64)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":'
        b'{"messageId":"m","role":"user","parts":[{"kind":"text","text":"'
        + (b"x" * 128)
        + b'"}]}}}'
    )

    async def _body_stream():
        yield body

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/", headers=headers, content=_body_stream())

    assert resp.status_code == 413
    assert resp.json() == {"error": "Request body too large", "max_bytes": 64}


@pytest.mark.asyncio
async def test_request_body_limit_preserves_body_for_downstream_handlers(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="test-token", a2a_max_request_body_bytes=4096)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            headers=headers,
            json=_jsonrpc_message_send_payload("hello after pre-read"),
        )

    assert resp.status_code == 200
    assert resp.json().get("error") is None


def test_create_app_propagates_cancel_abort_timeout(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    captured: dict[str, float | bool | int] = {}

    class _CapturingExecutor:
        def __init__(
            self,
            _client,
            *,
            streaming_enabled: bool,
            cancel_abort_timeout_seconds: float,
            session_cache_ttl_seconds: int,
            session_cache_maxsize: int,
        ) -> None:
            captured["streaming_enabled"] = streaming_enabled
            captured["cancel_abort_timeout_seconds"] = cancel_abort_timeout_seconds
            captured["session_cache_ttl_seconds"] = session_cache_ttl_seconds
            captured["session_cache_maxsize"] = session_cache_maxsize

        async def execute(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        async def cancel(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        def resolve_directory_for_control(self, requested: str | None) -> str | None:
            return requested

        async def claim_session_for_control(self, *, identity: str, session_id: str) -> bool:
            del identity, session_id
            return False

        async def finalize_session_for_control(self, *, identity: str, session_id: str) -> None:
            del identity, session_id

        async def release_session_for_control(self, *, identity: str, session_id: str) -> None:
            del identity, session_id

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    monkeypatch.setattr(app_module, "OpencodeAgentExecutor", _CapturingExecutor)

    app_module.create_app(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_cancel_abort_timeout_seconds=0.25,
            a2a_session_cache_ttl_seconds=11,
            a2a_session_cache_maxsize=22,
        )
    )

    assert captured["cancel_abort_timeout_seconds"] == 0.25
    assert captured["session_cache_ttl_seconds"] == 11
    assert captured["session_cache_maxsize"] == 22


def test_create_app_requires_control_guard_hooks(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    class _BrokenExecutor:
        def __init__(
            self,
            _client,
            *,
            streaming_enabled: bool,
            cancel_abort_timeout_seconds: float,
            session_cache_ttl_seconds: int,
            session_cache_maxsize: int,
        ) -> None:
            del (
                streaming_enabled,
                cancel_abort_timeout_seconds,
                session_cache_ttl_seconds,
                session_cache_maxsize,
            )
            self.claim_session_for_control = None

        async def execute(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        async def cancel(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        def resolve_directory_for_control(self, requested: str | None) -> str | None:
            return requested

        async def finalize_session_for_control(self, *, identity: str, session_id: str) -> None:
            del identity, session_id

        async def release_session_for_control(self, *, identity: str, session_id: str) -> None:
            del identity, session_id

    monkeypatch.setattr(app_module, "OpencodeClient", DummyChatOpencodeClient)
    monkeypatch.setattr(app_module, "OpencodeAgentExecutor", _BrokenExecutor)

    with pytest.raises(ValueError, match="Control methods require guard hooks"):
        app_module.create_app(make_settings(a2a_bearer_token="test-token"))

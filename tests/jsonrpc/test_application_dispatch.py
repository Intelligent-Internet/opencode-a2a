from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent
from a2a.utils.errors import InvalidParamsError, UnsupportedOperationError
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import opencode_a2a.server.application as app_module
from opencode_a2a.contracts.extensions import SESSION_MANAGEMENT_EXTENSION_URI
from opencode_a2a.jsonrpc.models import JSONRPCRequest
from tests.support.helpers import DummySessionQueryOpencodeUpstreamClient, make_settings
from tests.support.session_extensions import _BASE_SETTINGS, _jsonrpc_app


def _build_dispatcher(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummySessionQueryOpencodeUpstreamClient,
    )
    app = app_module.create_app(make_settings(test_bearer_token="test-token", **_BASE_SETTINGS))
    return _jsonrpc_app(app)


def _request_context() -> SimpleNamespace:
    return SimpleNamespace(state={}, tenant="")


async def _empty_stream() -> AsyncIterator[TaskStatusUpdateEvent]:
    if False:
        yield TaskStatusUpdateEvent(
            task_id="task-0",
            context_id="ctx-0",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )


async def _broken_stream() -> AsyncIterator[TaskStatusUpdateEvent]:
    yield TaskStatusUpdateEvent(
        task_id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    raise InvalidParamsError(message="bad stream")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "handler_name", "expected"),
    [
        ("CancelTask", "_handle_cancel_task", {"ok": "cancel"}),
        ("GetTask", "_handle_get_task", {"ok": "get"}),
        ("ListTasks", "_handle_list_tasks", {"ok": "list"}),
        (
            "CreateTaskPushNotificationConfig",
            "_handle_create_task_push_notification_config",
            {"ok": "create-push"},
        ),
        (
            "GetTaskPushNotificationConfig",
            "_handle_get_task_push_notification_config",
            {"ok": "get-push"},
        ),
        (
            "ListTaskPushNotificationConfigs",
            "_handle_list_task_push_notification_configs",
            {"ok": "list-push"},
        ),
        ("DeleteTaskPushNotificationConfig", "_handle_delete_task_push_notification_config", None),
        ("GetExtendedAgentCard", "_handle_get_extended_agent_card", {"ok": "extended-card"}),
    ],
)
async def test_process_non_streaming_request_dispatches_sdk_methods(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    handler_name: str,
    expected: dict[str, str] | None,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    handler = AsyncMock(return_value=expected)
    monkeypatch.setattr(dispatcher, handler_name, handler, raising=False)

    result = await dispatcher._process_non_streaming_request(
        object(),
        SimpleNamespace(state={"method": method}),
    )

    assert result == expected
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_non_streaming_request_rejects_unknown_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)

    with pytest.raises(UnsupportedOperationError, match="Method UnknownMethod is not supported."):
        await dispatcher._process_non_streaming_request(
            object(),
            SimpleNamespace(state={"method": "UnknownMethod"}),
        )


@pytest.mark.asyncio
async def test_process_streaming_request_supports_subscribe_and_empty_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    monkeypatch.setattr(
        dispatcher.request_handler,
        "on_subscribe_to_task",
        lambda _request_obj, _context: _empty_stream(),
    )

    wrapped = await dispatcher._process_streaming_request(
        9,
        object(),
        SimpleNamespace(state={"method": "SubscribeToTask"}),
    )

    assert [item async for item in wrapped] == []


@pytest.mark.asyncio
async def test_process_streaming_request_wraps_stream_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    monkeypatch.setattr(
        dispatcher.request_handler,
        "on_message_send_stream",
        lambda _request_obj, _context: _broken_stream(),
    )

    wrapped = await dispatcher._process_streaming_request(
        10,
        object(),
        SimpleNamespace(state={"method": "SendStreamingMessage"}),
    )
    payloads = [item async for item in wrapped]

    assert payloads[0]["id"] == 10
    assert payloads[0]["result"]["statusUpdate"]["taskId"] == "task-1"
    assert payloads[1]["error"]["code"] == -32602
    assert payloads[1]["error"]["message"] == "bad stream"


@pytest.mark.asyncio
async def test_process_streaming_request_rejects_non_stream_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)

    with pytest.raises(UnsupportedOperationError, match="Stream not supported"):
        await dispatcher._process_streaming_request(
            11,
            object(),
            SimpleNamespace(state={"method": "GetTask"}),
        )


@pytest.mark.asyncio
async def test_generate_protocol_error_response_supports_a2a_error_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    monkeypatch.setattr(
        "opencode_a2a.jsonrpc.application.adapt_jsonrpc_error",
        lambda _error: InvalidParamsError(
            message="bad request",
            data={"field": "params"},
        ),
    )

    response = dispatcher._generate_protocol_error_response(
        12,
        UnsupportedOperationError(),
        protocol_version="1.0",
    )

    assert response.status_code == 200
    assert response.body == (
        b'{"jsonrpc":"2.0","id":12,"error":{"code":-32602,'
        b'"message":"bad request","data":{"field":"params"}}}'
    )


@pytest.mark.asyncio
async def test_handle_core_request_supports_extended_card_notification_and_missing_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)

    notification = JSONRPCRequest.model_validate(
        {"jsonrpc": "2.0", "method": "GetExtendedAgentCard", "params": {}}
    )
    response = await dispatcher._handle_core_request(
        MagicMock(),
        {"params": {}},
        notification,
        protocol_version="1.0",
    )
    assert response.status_code == 204

    monkeypatch.setattr(dispatcher._http_handler, "extended_agent_card", None, raising=False)
    request = JSONRPCRequest.model_validate(
        {"jsonrpc": "2.0", "id": 13, "method": "GetExtendedAgentCard", "params": {}}
    )
    error_response = await dispatcher._handle_core_request(
        MagicMock(),
        {"params": {}},
        request,
        protocol_version="1.0",
    )

    assert error_response.status_code == 200
    assert b"The agent does not support authenticated extended cards" in error_response.body


@pytest.mark.asyncio
async def test_handle_core_request_returns_204_for_unknown_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    base_request = JSONRPCRequest.model_validate(
        {"jsonrpc": "2.0", "method": "NoSuchMethod", "params": {}}
    )

    response = await dispatcher._handle_core_request(
        MagicMock(),
        {"params": {}},
        base_request,
        protocol_version="1.0",
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_handle_core_request_invalid_params_and_handler_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    monkeypatch.setattr(dispatcher._context_builder, "build", lambda _request: _request_context())

    base_request = JSONRPCRequest.model_validate(
        {"jsonrpc": "2.0", "id": 14, "method": "GetTask", "params": {}}
    )
    invalid_response = await dispatcher._handle_core_request(
        MagicMock(),
        {"params": "bad"},
        base_request,
        protocol_version="1.0",
    )
    assert invalid_response.status_code == 200
    assert b'"code":-32602' in invalid_response.body

    monkeypatch.setattr(
        dispatcher,
        "_process_non_streaming_request",
        AsyncMock(side_effect=InvalidParamsError(message="handler failed")),
    )
    error_response = await dispatcher._handle_core_request(
        MagicMock(),
        {"params": {"id": "task-1"}},
        base_request,
        protocol_version="1.0",
    )
    assert error_response.status_code == 200
    assert error_response.body == (
        b'{"jsonrpc":"2.0","id":14,"error":{"code":-32602,"message":"Invalid parameters"}}'
    )


@pytest.mark.asyncio
async def test_handle_core_request_streaming_and_non_streaming_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    monkeypatch.setattr(dispatcher._context_builder, "build", lambda _request: _request_context())
    streaming_result = _empty_stream()
    create_response = MagicMock(return_value=JSONResponse({"stream": "ok"}))
    monkeypatch.setattr(
        dispatcher,
        "_process_streaming_request",
        AsyncMock(return_value=streaming_result),
    )
    monkeypatch.setattr(dispatcher, "_create_response", create_response)

    streaming_request = JSONRPCRequest.model_validate(
        {
            "jsonrpc": "2.0",
            "id": 15,
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "messageId": "msg-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                }
            },
        }
    )
    streaming_response = await dispatcher._handle_core_request(
        MagicMock(),
        {
            "params": {
                "message": {
                    "messageId": "msg-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                }
            }
        },
        streaming_request,
        protocol_version="1.0",
    )

    assert streaming_response.body == b'{"stream":"ok"}'
    create_response.assert_called_once()

    monkeypatch.setattr(
        dispatcher,
        "_process_non_streaming_request",
        AsyncMock(return_value={"ignored": True}),
    )
    notification = JSONRPCRequest.model_validate(
        {"jsonrpc": "2.0", "method": "GetTask", "params": {"id": "task-1"}}
    )
    response = await dispatcher._handle_core_request(
        MagicMock(),
        {"params": {"id": "task-1"}},
        notification,
        protocol_version="1.0",
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_handle_requests_normalizes_invalid_request_id_and_extension_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher = _build_dispatcher(monkeypatch)
    app = FastAPI()
    dispatcher.add_routes_to_app(app)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_id = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": {"bad": 1}, "method": "SendMessage", "params": {}},
        )
        invalid_id_payload = invalid_id.json()
        assert invalid_id_payload["id"] is None

    class _Request:
        def __init__(self) -> None:
            self.state = SimpleNamespace(a2a_protocol_version="1.0")

        async def json(self) -> dict[str, object]:
            return {
                "jsonrpc": "2.0",
                "id": 16,
                "method": "opencode.sessions.list",
                "params": "bad",
            }

    fake_base_request = SimpleNamespace(
        id=16,
        method="opencode.sessions.list",
        params="bad",
    )
    monkeypatch.setattr(
        "opencode_a2a.jsonrpc.application.JSONRPCRequest.model_validate",
        lambda _body: fake_base_request,
    )
    monkeypatch.setattr(
        dispatcher._context_builder,
        "build",
        lambda _request: SimpleNamespace(
            requested_extensions={SESSION_MANAGEMENT_EXTENSION_URI},
            state={},
            tenant="",
        ),
    )

    invalid_extension_response = await dispatcher.handle_requests(_Request())
    invalid_extension_payload = invalid_extension_response.body
    assert b'"code":-32602' in invalid_extension_payload
    assert b'"message":"Invalid parameters"' in invalid_extension_payload

import types
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.types import TaskState, TaskStatus, TaskStatusUpdateEvent, UnsupportedOperationError
from fastapi.responses import JSONResponse

import opencode_a2a.server.application as app_module
from opencode_a2a.a2a_protocol import V1_JSONRPC_METHOD_TO_LEGACY_METHOD
from opencode_a2a.jsonrpc.application import (
    OpencodeSessionManagementJSONRPCApplication,
    _normalize_core_message_part,
    _normalize_core_message_payload,
    _normalize_core_message_role,
    _normalize_core_request_params,
)
from tests.support.helpers import DummySessionQueryOpencodeUpstreamClient, make_settings
from tests.support.session_extensions import _BASE_SETTINGS, _jsonrpc_app


@pytest.mark.asyncio
async def test_extension_registry_tracks_configured_methods(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummySessionQueryOpencodeUpstreamClient,
    )
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_enable_session_shell=False,
            **_BASE_SETTINGS,
        )
    )

    registry_methods = _jsonrpc_app(app)._extension_method_registry.methods()  # noqa: SLF001
    assert "opencode.sessions.status" in registry_methods
    assert "opencode.sessions.list" in registry_methods
    assert "opencode.sessions.fork" in registry_methods
    assert "opencode.sessions.summarize" in registry_methods
    assert "opencode.sessions.revert" in registry_methods
    assert "opencode.sessions.unrevert" in registry_methods
    assert "opencode.providers.list" in registry_methods
    assert "opencode.projects.list" in registry_methods
    assert "opencode.permissions.list" in registry_methods
    assert "a2a.interrupt.permission.reply" in registry_methods
    assert "opencode.sessions.shell" not in registry_methods


@pytest.mark.asyncio
async def test_core_jsonrpc_methods_delegate_to_base_app(monkeypatch) -> None:
    async def _fake_core_handle(self, request, body, base_request, *, protocol_version):  # noqa: ANN001
        del self, request, body, protocol_version
        return JSONResponse(
            {
                "delegated_method": V1_JSONRPC_METHOD_TO_LEGACY_METHOD.get(
                    base_request.method,
                    base_request.method,
                )
            }
        )

    monkeypatch.setattr(
        OpencodeSessionManagementJSONRPCApplication,
        "_handle_core_request",
        _fake_core_handle,
    )
    app = app_module.create_app(make_settings(test_bearer_token="test-token", **_BASE_SETTINGS))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}},
        )

    assert response.status_code == 200
    assert response.json() == {"delegated_method": "message/send"}


@pytest.mark.asyncio
async def test_sdk_owned_non_chat_jsonrpc_methods_delegate_to_base_app(monkeypatch) -> None:
    async def _fake_core_handle(self, request, body, base_request, *, protocol_version):  # noqa: ANN001
        del self, request, body, protocol_version
        return JSONResponse(
            {
                "delegated_method": V1_JSONRPC_METHOD_TO_LEGACY_METHOD.get(
                    base_request.method,
                    base_request.method,
                )
            }
        )

    monkeypatch.setattr(
        OpencodeSessionManagementJSONRPCApplication,
        "_handle_core_request",
        _fake_core_handle,
    )
    app = app_module.create_app(make_settings(test_bearer_token="test-token", **_BASE_SETTINGS))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tasks/pushNotificationConfig/get",
                "params": {},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"delegated_method": "tasks/pushNotificationConfig/get"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alias_method", "canonical_method"),
    (
        ("SendMessage", "message/send"),
        ("SendStreamingMessage", "message/stream"),
        ("GetTask", "tasks/get"),
        ("CancelTask", "tasks/cancel"),
        ("GetExtendedAgentCard", "agent/getAuthenticatedExtendedCard"),
        ("GetTaskPushNotificationConfig", "tasks/pushNotificationConfig/get"),
        ("ListTaskPushNotificationConfigs", "tasks/pushNotificationConfig/list"),
        ("CreateTaskPushNotificationConfig", "tasks/pushNotificationConfig/set"),
        ("DeleteTaskPushNotificationConfig", "tasks/pushNotificationConfig/delete"),
    ),
)
async def test_v1_pascalcase_jsonrpc_aliases_delegate_to_canonical_methods(
    monkeypatch,
    alias_method: str,
    canonical_method: str,
) -> None:
    async def _fake_core_handle(self, request, body, base_request, *, protocol_version):  # noqa: ANN001
        del self, request, body, protocol_version
        return JSONResponse(
            {
                "delegated_method": V1_JSONRPC_METHOD_TO_LEGACY_METHOD.get(
                    base_request.method,
                    base_request.method,
                )
            }
        )

    monkeypatch.setattr(
        OpencodeSessionManagementJSONRPCApplication,
        "_handle_core_request",
        _fake_core_handle,
    )
    app = app_module.create_app(make_settings(test_bearer_token="test-token", **_BASE_SETTINGS))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Version": "1.0",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": alias_method, "params": {}},
        )

    assert response.status_code == 200
    assert response.headers["A2A-Version"] == "1.0"
    assert response.json() == {"delegated_method": canonical_method}


@pytest.mark.asyncio
async def test_extension_methods_stay_on_local_registry(monkeypatch) -> None:
    dummy = DummySessionQueryOpencodeUpstreamClient(
        make_settings(
            test_bearer_token="test-token",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    async def _unexpected_delegate(self, request, body, base_request, *, protocol_version):  # noqa: ANN001
        del self, request, body, base_request, protocol_version
        raise AssertionError("extension method should not delegate to base JSON-RPC app")

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    monkeypatch.setattr(
        OpencodeSessionManagementJSONRPCApplication,
        "_handle_core_request",
        _unexpected_delegate,
    )
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {"limit": 1},
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["items"][0]["id"] == "s-1"


def test_core_request_normalizers_cover_v1_message_shapes() -> None:
    assert _normalize_core_message_role(None) is None
    assert _normalize_core_message_role("user") == "ROLE_USER"
    assert _normalize_core_message_role("agent") == "ROLE_AGENT"
    assert _normalize_core_message_role("ROLE_USER") == "ROLE_USER"

    assert _normalize_core_message_part("plain") == "plain"
    assert _normalize_core_message_part({"kind": "text", "text": "hello"}) == {"text": "hello"}
    assert _normalize_core_message_part({"type": "data", "data": {"step": 1}}) == {
        "data": {"step": 1}
    }
    assert _normalize_core_message_part({"kind": "custom", "value": 1}) == {"value": 1}
    assert _normalize_core_message_part(
        {
            "kind": "file",
            "file": {"bytes": "aGVsbG8=", "name": "report.txt", "mimeType": "text/plain"},
        }
    ) == {
        "raw": "aGVsbG8=",
        "filename": "report.txt",
        "mediaType": "text/plain",
    }
    assert _normalize_core_message_part(
        {
            "kind": "file",
            "file": {
                "uri": "file:///tmp/report.txt",
                "name": "report.txt",
                "mediaType": "text/plain",
            },
        }
    ) == {
        "url": "file:///tmp/report.txt",
        "filename": "report.txt",
        "mediaType": "text/plain",
    }
    assert _normalize_core_message_part({"kind": "file", "url": "https://example.com"}) == {
        "url": "https://example.com"
    }

    assert _normalize_core_message_payload("raw-message") == "raw-message"
    assert _normalize_core_message_payload(
        {
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
        }
    ) == {
        "role": "ROLE_USER",
        "parts": [{"text": "hello"}],
    }
    assert _normalize_core_request_params("GetTask", {"id": "task-1"}) == {"id": "task-1"}
    assert _normalize_core_request_params(
        "SendMessage",
        {
            "message": {
                "role": "agent",
                "parts": [{"kind": "text", "text": "hello"}],
            }
        },
    ) == {
        "message": {
            "role": "ROLE_AGENT",
            "parts": [{"text": "hello"}],
        }
    }


@pytest.mark.asyncio
async def test_local_core_request_processors_cover_custom_v1_bypass(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummySessionQueryOpencodeUpstreamClient,
    )
    app = app_module.create_app(make_settings(test_bearer_token="test-token", **_BASE_SETTINGS))
    jsonrpc_app = _jsonrpc_app(app)

    handlers = {
        "SendMessage": "_handle_send_message",
        "CancelTask": "_handle_cancel_task",
        "GetTask": "_handle_get_task",
        "ListTasks": "_handle_list_tasks",
        "CreateTaskPushNotificationConfig": "_handle_create_task_push_notification_config",
        "GetTaskPushNotificationConfig": "_handle_get_task_push_notification_config",
        "ListTaskPushNotificationConfigs": "_handle_list_task_push_notification_configs",
        "GetExtendedAgentCard": "_handle_get_extended_agent_card",
    }
    for method, attr in handlers.items():
        mock = AsyncMock(return_value={"method": method})
        monkeypatch.setattr(jsonrpc_app, attr, mock)
        result = await jsonrpc_app._process_non_streaming_request(  # noqa: SLF001
            object(),
            types.SimpleNamespace(state={"method": method}),
        )
        assert result == {"method": method}
        mock.assert_awaited_once()

    delete_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(jsonrpc_app, "_handle_delete_task_push_notification_config", delete_mock)
    result = await jsonrpc_app._process_non_streaming_request(  # noqa: SLF001
        object(),
        types.SimpleNamespace(state={"method": "DeleteTaskPushNotificationConfig"}),
    )
    assert result is None
    delete_mock.assert_awaited_once()

    with pytest.raises(UnsupportedOperationError, match="Method MissingMethod is not supported"):
        await jsonrpc_app._process_non_streaming_request(  # noqa: SLF001
            object(),
            types.SimpleNamespace(state={"method": "MissingMethod"}),
        )

    async def _stream_then_error(_request_obj, _context):  # noqa: ANN001
        yield TaskStatusUpdateEvent(
            task_id="task-1",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
        raise UnsupportedOperationError(message="stream failed")

    async def _empty_stream(_request_obj, _context):  # noqa: ANN001
        if False:  # pragma: no cover
            yield None

    jsonrpc_app.request_handler.on_message_send_stream = _stream_then_error
    send_stream = await jsonrpc_app._process_streaming_request(  # noqa: SLF001
        7,
        object(),
        types.SimpleNamespace(state={"method": "SendStreamingMessage"}),
    )
    send_items = [item async for item in send_stream]
    assert send_items[0]["id"] == 7
    assert send_items[0]["jsonrpc"] == "2.0"
    assert "result" in send_items[0]
    assert send_items[1]["error"]["code"] == -32004

    jsonrpc_app.request_handler.on_subscribe_to_task = _empty_stream
    subscribe_stream = await jsonrpc_app._process_streaming_request(  # noqa: SLF001
        8,
        object(),
        types.SimpleNamespace(state={"method": "SubscribeToTask"}),
    )
    assert [item async for item in subscribe_stream] == []

    with pytest.raises(UnsupportedOperationError, match="Stream not supported"):
        await jsonrpc_app._process_streaming_request(  # noqa: SLF001
            9,
            object(),
            types.SimpleNamespace(state={"method": "UnknownStream"}),
        )

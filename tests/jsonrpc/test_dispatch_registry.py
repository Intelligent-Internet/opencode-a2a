import httpx
import pytest
from fastapi.responses import JSONResponse

import opencode_a2a.server.application as app_module
from opencode_a2a.a2a_protocol import CORE_JSONRPC_METHODS
from opencode_a2a.contracts.extensions import SESSION_MANAGEMENT_EXTENSION_URI
from opencode_a2a.jsonrpc.application import OpencodeSessionManagementJSONRPCApplication
from tests.support.helpers import DummySessionQueryOpencodeUpstreamClient, make_settings
from tests.support.jsonrpc_error_assertions import assert_v1_error_reason, error_context_detail
from tests.support.session_extensions import _BASE_SETTINGS, _extension_headers, _jsonrpc_app


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
@pytest.mark.parametrize("method", ("SendMessage", "SendStreamingMessage", "GetTask", "CancelTask"))
async def test_core_jsonrpc_methods_delegate_to_base_app(
    monkeypatch,
    method: str,
) -> None:
    async def _fake_core_handle(self, request, body, base_request, *, protocol_version):  # noqa: ANN001
        del self, request, body, protocol_version
        return JSONResponse({"delegated_method": base_request.method})

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
            headers=_extension_headers({"Authorization": "Bearer test-token"}),
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
        )

    assert response.status_code == 200
    assert response.json() == {"delegated_method": method}


@pytest.mark.asyncio
async def test_sdk_owned_non_chat_jsonrpc_methods_delegate_to_base_app(monkeypatch) -> None:
    async def _fake_core_handle(self, request, body, base_request, *, protocol_version):  # noqa: ANN001
        del self, request, body, protocol_version
        return JSONResponse({"delegated_method": base_request.method})

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
            headers=_extension_headers({"Authorization": "Bearer test-token"}),
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "GetTaskPushNotificationConfig",
                "params": {},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"delegated_method": "GetTaskPushNotificationConfig"}


def test_core_jsonrpc_methods_are_canonical_pascalcase() -> None:
    assert "SendMessage" in CORE_JSONRPC_METHODS
    assert "SendStreamingMessage" in CORE_JSONRPC_METHODS
    assert "GetTask" in CORE_JSONRPC_METHODS
    assert "CancelTask" in CORE_JSONRPC_METHODS
    assert "message/send" not in CORE_JSONRPC_METHODS
    assert "tasks/get" not in CORE_JSONRPC_METHODS


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
            headers=_extension_headers({"Authorization": "Bearer test-token"}),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {"limit": 1},
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["items"][0]["id"] == "s-1"


@pytest.mark.asyncio
async def test_extension_methods_require_explicit_a2a_extensions_header(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummySessionQueryOpencodeUpstreamClient,
    )
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_log_payloads=False,
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
                "id": 2,
                "method": "opencode.sessions.list",
                "params": {"limit": 1},
            },
        )

    assert response.status_code == 200
    error = response.json()["error"]
    assert_v1_error_reason(error, reason="EXTENSION_NEGOTIATION_REQUIRED")
    context = error_context_detail(error)
    assert context is not None
    assert context["method"] == "opencode.sessions.list"
    assert context["requiredExtensions"] == [SESSION_MANAGEMENT_EXTENSION_URI]  # noqa: RUF005
    assert context["requestedExtensions"] == []
    assert context["header"] == "A2A-Extensions"

import httpx
import pytest
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from fastapi.responses import JSONResponse

import opencode_a2a.server.application as app_module
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
            a2a_bearer_token="test-token",
            a2a_enable_session_shell=False,
            **_BASE_SETTINGS,
        )
    )

    registry_methods = _jsonrpc_app(app)._extension_method_registry.methods()  # noqa: SLF001
    assert "opencode.sessions.list" in registry_methods
    assert "opencode.providers.list" in registry_methods
    assert "a2a.interrupt.permission.reply" in registry_methods
    assert "opencode.sessions.shell" not in registry_methods


@pytest.mark.asyncio
async def test_core_jsonrpc_methods_delegate_to_base_app(monkeypatch) -> None:
    async def _fake_base_handle(self, request):  # noqa: ANN001
        payload = await request.json()
        return JSONResponse({"delegated_method": payload["method"]})

    monkeypatch.setattr(A2AFastAPIApplication, "_handle_requests", _fake_base_handle)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", **_BASE_SETTINGS))

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
    async def _fake_base_handle(self, request):  # noqa: ANN001
        payload = await request.json()
        return JSONResponse({"delegated_method": payload["method"]})

    monkeypatch.setattr(A2AFastAPIApplication, "_handle_requests", _fake_base_handle)
    app = app_module.create_app(make_settings(a2a_bearer_token="test-token", **_BASE_SETTINGS))

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
async def test_extension_methods_stay_on_local_registry(monkeypatch) -> None:
    dummy = DummySessionQueryOpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    async def _unexpected_delegate(self, request):  # noqa: ANN001
        raise AssertionError("extension method should not delegate to base JSON-RPC app")

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    monkeypatch.setattr(A2AFastAPIApplication, "_handle_requests", _unexpected_delegate)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="test-token",
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

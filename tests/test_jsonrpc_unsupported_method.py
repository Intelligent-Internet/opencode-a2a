import httpx
import pytest

from opencode_a2a_server.server.application import create_app
from tests.helpers import make_settings


@pytest.mark.asyncio
async def test_unsupported_method_returns_unified_error() -> None:
    settings = make_settings(a2a_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 123, "method": "unsupported.method", "params": {}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 123
    assert "error" in body
    error = body["error"]
    assert error["code"] == -32601
    assert "Unsupported method" in error["message"]

    data = error["data"]
    assert data["type"] == "METHOD_NOT_SUPPORTED"
    assert data["method"] == "unsupported.method"
    assert "supported_methods" in data
    assert "message/send" in data["supported_methods"]
    assert "opencode.sessions.list" in data["supported_methods"]
    assert data["protocol_version"] == settings.a2a_protocol_version


@pytest.mark.asyncio
async def test_unsupported_method_notification_returns_204() -> None:
    settings = make_settings(a2a_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "method": "unsupported.method", "params": {}},
        )

    # Even unsupported methods follow notification semantics: if id is missing, return 204.
    # Note: OpencodeSessionQueryJSONRPCApplication._handle_requests returns 204 for notifications
    # if it catches the method. For unsupported methods, it now also returns 204 if id is None.
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_disabled_shell_reports_current_supported_methods() -> None:
    settings = make_settings(a2a_bearer_token="test-token", a2a_enable_session_shell=False)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 124,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "pwd"},
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    error = body["error"]
    assert error["code"] == -32601
    assert error["data"]["type"] == "METHOD_NOT_SUPPORTED"
    assert error["data"]["method"] == "opencode.sessions.shell"
    assert "opencode.sessions.shell" not in error["data"]["supported_methods"]

import httpx
import pytest

from tests.support.helpers import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.helpers import make_settings
from tests.support.session_extensions import _BASE_SETTINGS


@pytest.mark.asyncio
async def test_workspace_control_extension_supports_read_only_methods(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        projects = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.projects.list", "params": {}},
        )
        current = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "opencode.projects.current",
                "params": {},
            },
        )
        workspaces = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "opencode.workspaces.list", "params": {}},
        )
        worktrees = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 4, "method": "opencode.worktrees.list", "params": {}},
        )

    assert projects.status_code == 200
    assert projects.json()["result"]["items"][0]["id"] == "proj-1"
    assert current.status_code == 200
    assert current.json()["result"]["item"]["id"] == "proj-1"
    assert workspaces.status_code == 200
    assert workspaces.json()["result"]["items"][0]["id"] == "wrk-1"
    assert worktrees.status_code == 200
    assert worktrees.json()["result"]["items"] == ["/tmp/worktrees/alpha"]


@pytest.mark.asyncio
async def test_workspace_control_extension_supports_mutating_methods(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        create_workspace = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "opencode.workspaces.create",
                "params": {"request": {"type": "git", "branch": "main"}},
            },
        )
        remove_workspace = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "opencode.workspaces.remove",
                "params": {"workspace_id": "wrk-1"},
            },
        )
        create_worktree = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "opencode.worktrees.create",
                "params": {"request": {"name": "feature-branch"}},
            },
        )
        remove_worktree = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "opencode.worktrees.remove",
                "params": {"request": {"directory": "/tmp/worktrees/feature-branch"}},
            },
        )
        reset_worktree = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 14,
                "method": "opencode.worktrees.reset",
                "params": {"request": {"directory": "/tmp/worktrees/feature-branch"}},
            },
        )

    assert create_workspace.status_code == 200
    assert create_workspace.json()["result"]["item"]["type"] == "git"
    assert remove_workspace.status_code == 200
    assert remove_workspace.json()["result"]["item"]["id"] == "wrk-1"
    assert create_worktree.status_code == 200
    assert create_worktree.json()["result"]["item"]["directory"] == "/tmp/worktrees/feature-branch"
    assert remove_worktree.status_code == 200
    assert remove_worktree.json()["result"] == {"ok": True}
    assert reset_worktree.status_code == 200
    assert reset_worktree.json()["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_workspace_control_extension_validates_request_shape(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "opencode.workspaces.create",
                "params": {"request": {"branch": "main"}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32602
    assert payload["error"]["data"]["field"] == "request"


@pytest.mark.asyncio
async def test_workspace_control_extension_maps_upstream_http_error(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    class UpstreamErrorClient(DummyOpencodeUpstreamClient):
        async def list_workspaces(self):
            request = httpx.Request("GET", "http://test/experimental/workspace")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", UpstreamErrorClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "opencode.workspaces.list",
                "params": {},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["data"]["type"] == "UPSTREAM_HTTP_ERROR"
    assert payload["error"]["data"]["upstream_status"] == 503

import httpx
import pytest

from tests.support.helpers import make_basic_auth_header
from tests.support.jsonrpc_error_assertions import (
    assert_v1_error_metadata_contains,
    assert_v1_error_reason,
)
from tests.support.session_extensions import _BASE_SETTINGS, _extension_headers
from tests.support.session_query_client import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.settings import make_settings


@pytest.mark.asyncio
async def test_workspace_control_extension_supports_read_only_methods(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers({"Authorization": "Bearer t-1"})
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
    assert projects.json()["result"]["items"][0] == {
        "id": "proj-1",
        "name": "Alpha",
        "vcs": "git",
    }
    assert current.status_code == 200
    assert current.json()["result"]["item"] == {
        "id": "proj-1",
        "name": "Alpha",
        "vcs": "git",
    }
    assert workspaces.status_code == 200
    assert workspaces.json()["result"]["items"][0] == {
        "id": "wrk-1",
        "type": "git",
        "name": "Alpha workspace",
        "branch": "main",
    }
    assert worktrees.status_code == 200
    assert worktrees.json()["result"]["items"] == [{"name": "alpha", "branch": "opencode/alpha"}]


@pytest.mark.asyncio
async def test_workspace_control_discovery_responses_exclude_internal_fields(
    monkeypatch,
) -> None:
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers({"Authorization": "Bearer t-1"})
        responses = []
        for method, request_id in (
            ("opencode.projects.list", 1),
            ("opencode.projects.current", 2),
            ("opencode.workspaces.list", 3),
            ("opencode.worktrees.list", 4),
        ):
            response = await client.post(
                "/",
                headers=headers,
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}},
            )
            assert response.status_code == 200
            responses.append(response.json())

    for payload in responses:
        serialized = str(payload)
        assert "directory" not in serialized
        assert "canonical" not in serialized
        assert "apiKey" not in serialized
        assert "sk-secret" not in serialized
        assert "icon" not in serialized
        assert "internal.local" not in serialized
        assert "/workspace" not in serialized
        assert "/tmp/worktrees" not in serialized


@pytest.mark.asyncio
async def test_workspace_control_extension_supports_mutating_methods(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="t-1",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
            a2a_log_payloads=False,
            a2a_enable_workspace_mutations=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers(make_basic_auth_header("operator", "op-pass"))
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
    assert "directory" not in str(create_workspace.json())
    assert remove_workspace.status_code == 200
    assert remove_workspace.json()["result"]["item"]["id"] == "wrk-1"
    assert "directory" not in str(remove_workspace.json())
    assert create_worktree.status_code == 200
    assert create_worktree.json()["result"]["item"] == {
        "name": "feature-branch",
        "branch": "opencode/feature-branch",
    }
    assert remove_worktree.status_code == 200
    assert remove_worktree.json()["result"] == {"ok": True}
    assert reset_worktree.status_code == 200
    assert reset_worktree.json()["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_workspace_control_extension_validates_request_shape(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "bearer",
                    "token": "t-1",
                    "principal": "automation",
                    "credential_id": "cred-bearer",
                },
                {
                    "scheme": "basic",
                    "username": "operator",
                    "password": "op-pass",  # pragma: allowlist secret
                },
            ),
            a2a_log_payloads=False,
            a2a_enable_workspace_mutations=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=_extension_headers(make_basic_auth_header("operator", "op-pass")),
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
async def test_workspace_control_mutations_require_workspace_mutation_capability(
    monkeypatch,
) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "bearer",
                    "token": "t-1",
                    "principal": "automation",
                    "credential_id": "cred-bearer",
                },
                {
                    "scheme": "basic",
                    "username": "operator",
                    "password": "op-pass",  # pragma: allowlist secret
                },
            ),
            a2a_log_payloads=False,
            a2a_enable_workspace_mutations=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer t-1"}),
            json={
                "jsonrpc": "2.0",
                "id": 201,
                "method": "opencode.workspaces.create",
                "params": {"request": {"type": "git", "branch": "main"}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32007
    assert_v1_error_reason(
        payload["error"],
        reason="AUTHORIZATION_FORBIDDEN",
        metadata={
            "method": "opencode.workspaces.create",
            "capability": "workspace_mutation",
            "credential_id": "cred-bearer",
        },
    )


@pytest.mark.asyncio
async def test_workspace_control_mutations_are_disabled_by_default(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer t-1"}),
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "opencode.worktrees.create",
                "params": {"request": {"name": "feature-branch"}},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["code"] == -32601
    assert payload["error"]["data"]["method"] == "opencode.worktrees.create"


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
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer t-1"}),
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "opencode.workspaces.list",
                "params": {},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert_v1_error_metadata_contains(
        payload["error"],
        reason="UPSTREAM_HTTP_ERROR",
        metadata={"upstream_status": 503},
    )

import httpx
import pytest

from tests.helpers import DummySessionQueryOpencodeClient as DummyOpencodeClient
from tests.helpers import make_settings
from tests.session_extension_fixtures import _BASE_SETTINGS, _session_meta


@pytest.mark.asyncio
async def test_session_command_extension_success(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 320,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "/review", "arguments": "security"},
                    "metadata": {"opencode": {"directory": "/workspace"}},
                },
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("error") is None
        assert payload["result"]["item"]["messageId"] == "msg-command-1"
        assert _session_meta(payload["result"]["item"])["id"] == "s-1"
        assert payload["result"]["item"]["parts"][0]["text"] == "Command completed."
        assert len(dummy.command_calls) == 1
        assert dummy.command_calls[0]["session_id"] == "s-1"
        assert dummy.command_calls[0]["directory"] == "/workspace"


@pytest.mark.asyncio
async def test_session_command_extension_accepts_request_model(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3201,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "/review",
                        "arguments": "security",
                        "model": {
                            "providerID": "openai",
                            "modelID": "gpt-5",
                        },
                    },
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("error") is None
        assert dummy.command_calls[0]["request"]["model"] == {
            "providerID": "openai",
            "modelID": "gpt-5",
        }


@pytest.mark.asyncio
async def test_session_command_extension_rejects_invalid_params(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        missing_command = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 321,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"arguments": "security"},
                },
            },
        )
        payload = missing_command.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.command"

        bad_arguments = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 322,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "/review", "arguments": 123},
                },
            },
        )
        payload = bad_arguments.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.arguments"

        bad_model = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3221,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "/review",
                        "arguments": "security",
                        "model": {"providerID": "openai"},
                    },
                },
            },
        )
        payload = bad_model.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.model.modelID"


@pytest.mark.asyncio
async def test_session_command_extension_maps_404_to_session_not_found(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class NotFoundCommandClient(DummyOpencodeClient):
        async def session_command(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-404/command")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeClient", NotFoundCommandClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 323,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-404",
                    "request": {"command": "/review", "arguments": "security"},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32001
        assert payload["error"]["data"]["type"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_shell_extension_disabled_by_default(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 330,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "git status"},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32601
        assert payload["error"]["data"]["type"] == "METHOD_NOT_SUPPORTED"
        assert "opencode.sessions.shell" not in payload["error"]["data"]["supported_methods"]
        assert dummy.shell_calls == []


@pytest.mark.asyncio
async def test_session_shell_extension_success_when_enabled(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 331,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "git status --short"},
                    "metadata": {"opencode": {"directory": "/workspace"}},
                },
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("error") is None
        assert payload["result"]["item"]["messageId"] == "msg-shell-1"
        assert _session_meta(payload["result"]["item"])["id"] == "s-1"
        assert payload["result"]["item"]["parts"][0]["text"] == "Shell command executed."
        assert len(dummy.shell_calls) == 1
        assert dummy.shell_calls[0]["directory"] == "/workspace"


@pytest.mark.asyncio
async def test_session_shell_extension_rejects_invalid_params(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        missing_agent = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 332,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "git status --short"},
                },
            },
        )
        payload = missing_agent.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.agent"

        bad_model = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 333,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "agent": "code-reviewer",
                        "command": "git status --short",
                        "model": {"providerID": "openai"},
                    },
                },
            },
        )
        payload = bad_model.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.model.modelID"


@pytest.mark.asyncio
async def test_session_shell_extension_rejects_owner_mismatch(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    app.state.opencode_agent_executor._session_owners.set("s-1", "bearer:other")  # noqa: SLF001

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 334,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "git status --short"},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32006
        assert payload["error"]["data"]["type"] == "SESSION_FORBIDDEN"
        assert dummy.shell_calls == []


@pytest.mark.asyncio
async def test_session_command_extension_maps_500_to_upstream_http_error(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class UpstreamErrorCommandClient(DummyOpencodeClient):
        async def session_command(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/command")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("Internal Server Error", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeClient", UpstreamErrorCommandClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 335,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "/review", "arguments": "security"},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32003
        assert payload["error"]["data"]["type"] == "UPSTREAM_HTTP_ERROR"
        assert payload["error"]["data"]["upstream_status"] == 500


@pytest.mark.asyncio
async def test_session_shell_extension_maps_network_error_to_unreachable(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class NetworkErrorShellClient(DummyOpencodeClient):
        async def session_shell(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/shell")
            raise httpx.ConnectError("network down", request=req)

    monkeypatch.setattr(app_module, "OpencodeClient", NetworkErrorShellClient)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 336,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "git status --short"},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32002
        assert payload["error"]["data"]["type"] == "UPSTREAM_UNREACHABLE"

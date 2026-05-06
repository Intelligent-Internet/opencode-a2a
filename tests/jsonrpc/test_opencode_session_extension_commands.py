import httpx
import pytest

from tests.support.helpers import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.helpers import make_basic_auth_header
from tests.support.jsonrpc_error_assertions import (
    assert_v1_error_context,
    assert_v1_error_metadata_contains,
    assert_v1_error_reason,
)
from tests.support.session_extensions import (
    _BASE_SETTINGS,
    _extension_headers,
    _jsonrpc_app,
    _session_meta,
)
from tests.support.settings import make_settings


@pytest.mark.asyncio
async def test_session_command_extension_success(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            test_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers({"Authorization": "Bearer t-1"})
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
async def test_session_command_extension_uses_registry_bearer_principal(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "bearer",
                    "token": "token-build",
                    "principal": "build-bot",
                },
                {
                    "scheme": "bearer",
                    "token": "token-other",
                    "principal": "other-bot",
                },
            ),
            a2a_log_payloads=False,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "bearer",
                    "token": "token-build",
                    "principal": "build-bot",
                },
                {
                    "scheme": "bearer",
                    "token": "token-other",
                    "principal": "other-bot",
                },
            ),
            a2a_log_payloads=False,
            **_BASE_SETTINGS,
        )
    )
    await _jsonrpc_app(app)._session_claim_finalize(  # noqa: SLF001
        identity="build-bot",
        session_id="s-1",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        owned = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer token-build"}),
            json={
                "jsonrpc": "2.0",
                "id": 32001,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "/review", "arguments": "security"},
                },
            },
        )
        foreign = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer token-other"}),
            json={
                "jsonrpc": "2.0",
                "id": 32002,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "/review", "arguments": "security"},
                },
            },
        )

    assert owned.status_code == 200
    assert owned.json().get("error") is None
    assert_v1_error_reason(
        foreign.json()["error"],
        reason="SESSION_FORBIDDEN",
        metadata={"session_id": "s-1"},
    )


@pytest.mark.asyncio
async def test_session_command_extension_prefers_workspace_metadata(monkeypatch):
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
        response = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer t-1"}),
            json={
                "jsonrpc": "2.0",
                "id": 3202,
                "method": "opencode.sessions.command",
                "params": {
                    "session_id": "s-1",
                    "request": {"command": "/review", "arguments": "security"},
                    "metadata": {"opencode": {"workspace": {"id": "wrk-1"}}},
                },
            },
        )

    assert response.status_code == 200
    assert dummy.command_calls[0]["directory"] is None
    assert dummy.command_calls[0]["workspace_id"] == "wrk-1"


@pytest.mark.asyncio
async def test_session_command_extension_accepts_request_model(monkeypatch):
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
    import opencode_a2a.server.application as app_module

    class NotFoundCommandClient(DummyOpencodeUpstreamClient):
        async def session_command(
            self, session_id: str, request: dict, *, directory=None, workspace_id=None
        ):
            del session_id, request, directory, workspace_id
            req = httpx.Request("POST", "http://opencode/session/s-404/command")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NotFoundCommandClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers({"Authorization": "Bearer t-1"})
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
        assert_v1_error_reason(
            payload["error"],
            reason="SESSION_NOT_FOUND",
            metadata={"session_id": "s-404"},
        )


@pytest.mark.asyncio
async def test_session_shell_extension_disabled_by_default(monkeypatch):
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
        assert payload["error"]["data"]["method"] == "opencode.sessions.shell"
        assert "opencode.sessions.shell" not in payload["error"]["data"]["supportedMethods"]
        assert dummy.shell_calls == []


@pytest.mark.asyncio
async def test_session_shell_extension_success_when_enabled(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            test_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="t-1",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers(make_basic_auth_header("operator", "op-pass"))
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
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            test_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="t-1",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers(make_basic_auth_header("operator", "op-pass"))
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
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            test_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="t-1",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    await _jsonrpc_app(app)._session_claim_finalize(  # noqa: SLF001
        identity="other-user",
        session_id="s-1",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers(make_basic_auth_header("operator", "op-pass"))
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
        assert_v1_error_reason(
            payload["error"],
            reason="SESSION_FORBIDDEN",
            metadata={"session_id": "s-1"},
        )
        assert dummy.shell_calls == []


@pytest.mark.asyncio
async def test_session_shell_extension_requires_session_shell_capability(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
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
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
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
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer t-1"}),
            json={
                "jsonrpc": "2.0",
                "id": 3341,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "git status --short"},
                },
            },
        )

    payload = resp.json()
    assert payload["error"]["code"] == -32007
    assert_v1_error_reason(
        payload["error"],
        reason="AUTHORIZATION_FORBIDDEN",
        metadata={
            "method": "opencode.sessions.shell",
            "capability": "session_shell",
            "credential_id": "cred-bearer",
        },
    )
    assert dummy.shell_calls == []


@pytest.mark.asyncio
async def test_session_shell_extension_accepts_registry_bearer_with_explicit_capability(
    monkeypatch,
):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "bearer",
                    "token": "token-ops",
                    "principal": "ops-bot",
                    "capabilities": ["session_shell"],
                },
            ),
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings, **_kwargs: dummy)
    app = app_module.create_app(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "bearer",
                    "token": "token-ops",
                    "principal": "ops-bot",
                    "capabilities": ["session_shell"],
                },
            ),
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )
    await _jsonrpc_app(app)._session_claim_finalize(  # noqa: SLF001
        identity="ops-bot",
        session_id="s-1",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer token-ops"}),
            json={
                "jsonrpc": "2.0",
                "id": 3342,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "git status --short"},
                },
            },
        )

    assert resp.status_code == 200
    assert resp.json().get("error") is None
    assert len(dummy.shell_calls) == 1


@pytest.mark.asyncio
async def test_session_command_extension_maps_500_to_upstream_http_error(monkeypatch):
    import opencode_a2a.server.application as app_module

    class UpstreamErrorCommandClient(DummyOpencodeUpstreamClient):
        async def session_command(
            self, session_id: str, request: dict, *, directory=None, workspace_id=None
        ):
            del session_id, request, directory, workspace_id
            req = httpx.Request("POST", "http://opencode/session/s-1/command")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("Internal Server Error", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", UpstreamErrorCommandClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers({"Authorization": "Bearer t-1"})
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
        assert_v1_error_metadata_contains(
            payload["error"],
            reason="UPSTREAM_HTTP_ERROR",
            metadata={"upstream_status": 500},
        )


@pytest.mark.asyncio
async def test_session_shell_extension_maps_network_error_to_unreachable(monkeypatch):
    import opencode_a2a.server.application as app_module

    class NetworkErrorShellClient(DummyOpencodeUpstreamClient):
        async def session_shell(
            self, session_id: str, request: dict, *, directory=None, workspace_id=None
        ):
            del session_id, request, directory, workspace_id
            req = httpx.Request("POST", "http://opencode/session/s-1/shell")
            raise httpx.ConnectError("network down", request=req)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NetworkErrorShellClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="t-1",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = _extension_headers(make_basic_auth_header("operator", "op-pass"))
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
        assert_v1_error_reason(
            payload["error"],
            reason="UPSTREAM_UNREACHABLE",
            metadata={"method": "opencode.sessions.shell", "session_id": "s-1"},
        )
        assert_v1_error_context(
            payload["error"],
            metadata={"method": "opencode.sessions.shell", "session_id": "s-1"},
        )

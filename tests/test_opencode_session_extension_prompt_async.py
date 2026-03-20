import logging

import httpx
import pytest

from opencode_a2a_server.opencode_upstream_client import UpstreamContractError
from tests.helpers import DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient
from tests.helpers import make_settings
from tests.session_extension_fixtures import _BASE_SETTINGS


@pytest.mark.asyncio
async def test_session_prompt_async_extension_success(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
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
                "id": 301,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "Continue the task"}],
                        "noReply": True,
                    },
                    "metadata": {"opencode": {"directory": "/workspace"}},
                },
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("error") is None
        assert payload["result"] == {"ok": True, "session_id": "s-1"}
        assert len(dummy.prompt_async_calls) == 1
        assert dummy.prompt_async_calls[0]["session_id"] == "s-1"
        assert dummy.prompt_async_calls[0]["directory"] == "/workspace"
        assert dummy.prompt_async_calls[0]["request"]["parts"][0]["text"] == "Continue the task"


@pytest.mark.asyncio
async def test_session_prompt_async_extension_rejects_invalid_params(monkeypatch):
    import opencode_a2a_server.server.application as app_module

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

        missing_session_id = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 302,
                "method": "opencode.sessions.prompt_async",
                "params": {"request": {"parts": [{"type": "text", "text": "x"}]}},
            },
        )
        payload = missing_session_id.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "session_id"

        invalid_request = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 303,
                "method": "opencode.sessions.prompt_async",
                "params": {"session_id": "s-1", "request": "invalid"},
            },
        )
        payload = invalid_request.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request"

        missing_parts = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 304,
                "method": "opencode.sessions.prompt_async",
                "params": {"session_id": "s-1", "request": {"agent": "code-reviewer"}},
            },
        )
        payload = missing_parts.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.parts"

        bad_field = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 309,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "hello"}],
                        "foo": "bar",
                    },
                },
            },
        )
        payload = bad_field.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request"

        bad_model = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 310,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "hello"}],
                        "model": {"providerID": "openai"},
                    },
                },
            },
        )
        payload = bad_model.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "request.model.modelID"


@pytest.mark.asyncio
async def test_session_prompt_async_extension_rejects_owner_mismatch(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
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
                "id": 311,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32006
        assert payload["error"]["data"]["type"] == "SESSION_FORBIDDEN"
        assert dummy.prompt_async_calls == []


@pytest.mark.asyncio
async def test_session_prompt_async_extension_reuses_directory_boundary_validation(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/safe/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/safe/workspace",
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
                "id": 312,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                    "metadata": {"opencode": {"directory": "../escape"}},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "metadata.opencode.directory"
        assert dummy.prompt_async_calls == []


@pytest.mark.asyncio
async def test_session_prompt_async_extension_honors_directory_override_switch(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/safe/workspace",
            a2a_allow_directory_override=False,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/safe/workspace",
            a2a_allow_directory_override=False,
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
                "id": 313,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                    "metadata": {"opencode": {"directory": "/safe/workspace/subdir"}},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "metadata.opencode.directory"
        assert dummy.prompt_async_calls == []


@pytest.mark.asyncio
async def test_session_prompt_async_extension_maps_404_to_session_not_found(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class NotFoundPromptAsyncClient(DummyOpencodeUpstreamClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-404/prompt_async")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NotFoundPromptAsyncClient)
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
                "id": 305,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-404",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32001
        assert payload["error"]["data"]["type"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_prompt_async_extension_maps_non_204_to_payload_error(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class InvalidPromptAsyncStatusClient(DummyOpencodeUpstreamClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            raise UpstreamContractError(
                "OpenCode /session/{sessionID}/prompt_async must return 204; got 200"
            )

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", InvalidPromptAsyncStatusClient)
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
                "id": 306,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


@pytest.mark.asyncio
async def test_session_prompt_async_extension_maps_500_to_upstream_http_error(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class UpstreamErrorPromptAsyncClient(DummyOpencodeUpstreamClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/prompt_async")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("Internal Server Error", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", UpstreamErrorPromptAsyncClient)
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
                "id": 307,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32003
        assert payload["error"]["data"]["type"] == "UPSTREAM_HTTP_ERROR"
        assert payload["error"]["data"]["upstream_status"] == 500


@pytest.mark.asyncio
async def test_session_prompt_async_extension_maps_network_error_to_unreachable(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class NetworkErrorPromptAsyncClient(DummyOpencodeUpstreamClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/prompt_async")
            raise httpx.ConnectError("network down", request=req)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NetworkErrorPromptAsyncClient)
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
                "id": 308,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32002
        assert payload["error"]["data"]["type"] == "UPSTREAM_UNREACHABLE"


@pytest.mark.asyncio
async def test_session_prompt_async_release_failure_does_not_override_response(monkeypatch, caplog):
    import opencode_a2a_server.server.application as app_module
    from opencode_a2a_server.execution.executor import OpencodeAgentExecutor

    class NetworkErrorPromptAsyncClient(DummyOpencodeUpstreamClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/prompt_async")
            raise httpx.ConnectError("network down", request=req)

    async def _release_raises(
        self: OpencodeAgentExecutor, *, identity: str, session_id: str
    ) -> None:
        del identity, session_id
        raise RuntimeError("release failed")

    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NetworkErrorPromptAsyncClient)
    monkeypatch.setattr(OpencodeAgentExecutor, "release_session_for_control", _release_raises)
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
                "id": 3081,
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "x"}]},
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32002
        assert payload["error"]["data"]["type"] == "UPSTREAM_UNREACHABLE"

    assert any(
        "Failed to release pending session claim" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_session_prompt_async_extension_notification_returns_204(monkeypatch):
    import opencode_a2a_server.server.application as app_module

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
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "hello"}]},
                },
            },
        )
        assert resp.status_code == 204
        assert len(dummy.prompt_async_calls) == 1

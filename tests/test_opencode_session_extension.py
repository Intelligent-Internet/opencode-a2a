import logging

import httpx
import pytest

from opencode_a2a_server.config import Settings
from opencode_a2a_server.opencode_client import UpstreamContractError
from tests.helpers import DummySessionQueryOpencodeClient as DummyOpencodeClient
from tests.helpers import make_settings

_BASE_SETTINGS = {
    "opencode_timeout": 1.0,
    "a2a_log_level": "DEBUG",
}


def _session_meta(payload: dict) -> dict:
    return payload["metadata"]["shared"]["session"]


@pytest.mark.asyncio
async def test_session_query_extension_requires_bearer_token(monkeypatch):
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.sessions.list", "params": {}},
        )
        assert resp.status_code == 401

        resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_session_query_extension_returns_jsonrpc_result(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
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
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {"limit": 10},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert "raw" not in payload["result"]
        session = payload["result"]["items"][0]
        assert session["id"] == "s-1"
        assert session["contextId"] == "ctx:opencode-session:s-1"
        assert session["contextId"] != _session_meta(session)["id"]
        assert _session_meta(session)["id"] == "s-1"
        assert _session_meta(session)["title"] == "Session s-1"
        assert "raw" not in session["metadata"]["shared"]
        assert dummy.last_sessions_params is not None
        assert dummy.last_sessions_params.get("limit") == 10

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1", "limit": 5},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert "raw" not in payload["result"]
        message = payload["result"]["items"][0]
        assert message["contextId"] == "ctx:opencode-session:s-1"
        assert message["parts"][0]["text"] == "SECRET_HISTORY"
        assert _session_meta(message)["id"] == "s-1"
        assert dummy.last_messages_params is not None
        assert dummy.last_messages_params.get("limit") == 5


@pytest.mark.asyncio
async def test_provider_discovery_extension_returns_normalized_catalog(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        providers_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "opencode.providers.list",
                "params": {},
            },
        )
        assert providers_resp.status_code == 200
        providers_payload = providers_resp.json()["result"]
        assert providers_payload["default_by_provider"]["openai"] == "gpt-5"
        assert providers_payload["connected"] == ["openai"]
        assert providers_payload["items"][0]["provider_id"] == "openai"
        assert providers_payload["items"][0]["default_model_id"] == "gpt-5"

        models_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "opencode.models.list",
                "params": {"provider_id": "google"},
            },
        )
        assert models_resp.status_code == 200
        models_payload = models_resp.json()["result"]
        assert len(models_payload["items"]) == 1
        assert models_payload["items"][0]["provider_id"] == "google"
        assert models_payload["items"][0]["model_id"] == "gemini-2.5-flash"
        assert models_payload["items"][0]["supports_attachments"] is True


@pytest.mark.asyncio
async def test_provider_discovery_extension_rejects_invalid_provider_id(monkeypatch):
    import opencode_a2a_server.app as app_module

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
                "id": 13,
                "method": "opencode.models.list",
                "params": {"provider_id": 123},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "provider_id"


@pytest.mark.asyncio
async def test_provider_discovery_extension_maps_payload_mismatch(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.provider_catalog_payload = {"all": "bad", "default": {}, "connected": []}
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
                "id": 14,
                "method": "opencode.providers.list",
                "params": {},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


@pytest.mark.asyncio
async def test_session_query_extension_rejects_non_array_upstream_payload(monkeypatch):
    import opencode_a2a_server.app as app_module

    class WeirdPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"foo": "bar"}  # no items

    monkeypatch.setattr(app_module, "OpencodeClient", WeirdPayloadClient)
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
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


@pytest.mark.asyncio
async def test_session_query_extension_session_title_is_extracted_or_placeholder(monkeypatch):
    import opencode_a2a_server.app as app_module

    class TitlePayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "My Session"}]

    monkeypatch.setattr(app_module, "OpencodeClient", TitlePayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.sessions.list", "params": {}},
        )
        payload = resp.json()
        session = payload["result"]["items"][0]
        assert session["id"] == "s-1"
        assert _session_meta(session)["title"] == "My Session"


@pytest.mark.asyncio
async def test_session_query_extension_keeps_session_with_empty_title(monkeypatch):
    import opencode_a2a_server.app as app_module

    class EmptyTitlePayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "   "}]

    monkeypatch.setattr(app_module, "OpencodeClient", EmptyTitlePayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.sessions.list", "params": {}},
        )
        payload = resp.json()
        session = payload["result"]["items"][0]
        assert session["id"] == "s-1"
        assert _session_meta(session)["title"] == ""


@pytest.mark.asyncio
async def test_session_query_extension_message_role_and_id_from_info(monkeypatch):
    import opencode_a2a_server.app as app_module

    class InfoRoleClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._messages_payload = [
                {
                    "info": {"id": "msg-1", "role": "user"},
                    "parts": [{"type": "text", "text": "hello"}],
                }
            ]

    monkeypatch.setattr(app_module, "OpencodeClient", InfoRoleClient)
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
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        payload = resp.json()
        message = payload["result"]["items"][0]
        assert message["messageId"] == "msg-1"
        assert message["role"] == "user"
        assert message["parts"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_session_query_extension_accepts_top_level_list_payload(monkeypatch):
    import opencode_a2a_server.app as app_module

    class ListPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "s1"}]
            self._messages_payload = [
                {
                    "info": {"id": "m-1", "role": "assistant"},
                    "parts": [{"type": "text", "text": "SECRET_HISTORY"}],
                }
            ]

    monkeypatch.setattr(app_module, "OpencodeClient", ListPayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.sessions.list", "params": {}},
        )
        payload = resp.json()
        assert payload["result"]["items"][0]["id"] == "s-1"
        assert payload["result"]["items"][0]["contextId"] == "ctx:opencode-session:s-1"
        assert _session_meta(payload["result"]["items"][0])["id"] == "s-1"

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        payload = resp.json()
        assert payload["result"]["items"][0]["contextId"] == "ctx:opencode-session:s-1"
        assert _session_meta(payload["result"]["items"][0])["id"] == "s-1"
        assert payload["result"]["items"][0]["parts"][0]["text"] == "SECRET_HISTORY"


@pytest.mark.asyncio
async def test_session_query_extension_rejects_non_list_wrapped_payload(monkeypatch):
    import opencode_a2a_server.app as app_module

    class AltKeyPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"sessions": [{"id": "s-1"}]}
            self._messages_payload = {"messages": [{"id": "m-1", "text": "SECRET_HISTORY"}]}

    monkeypatch.setattr(app_module, "OpencodeClient", AltKeyPayloadClient)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.sessions.list", "params": {}},
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32005
        assert payload["error"]["data"]["type"] == "UPSTREAM_PAYLOAD_ERROR"


@pytest.mark.asyncio
async def test_session_query_extension_rejects_cursor_limit(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
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
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {"cursor": "abc", "limit": 10},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_session_query_extension_rejects_page_size_pagination(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
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
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {"page": 1, "size": 1000},
            },
        )
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_session_query_extension_maps_404_to_session_not_found(monkeypatch):
    import opencode_a2a_server.app as app_module

    class NotFoundOpencodeClient(DummyOpencodeClient):
        async def list_messages(self, session_id: str, *, params=None):
            request = httpx.Request("GET", "http://opencode/session/x/message")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    monkeypatch.setattr(app_module, "OpencodeClient", NotFoundOpencodeClient)
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
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-404"},
            },
        )
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert payload["error"]["code"] == -32001
        assert payload["error"]["data"]["type"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_query_extension_does_not_log_response_bodies(monkeypatch, caplog):
    import opencode_a2a_server.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    caplog.set_level(logging.DEBUG, logger="opencode_a2a_server.app")

    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=True, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1"},
            },
        )
        assert resp.status_code == 200

    # The response contains SECRET_HISTORY but the log middleware must not print bodies for
    # opencode.sessions.* operations.
    assert "SECRET_HISTORY" not in caplog.text


@pytest.mark.asyncio
async def test_session_prompt_async_extension_success(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
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
    import opencode_a2a_server.app as app_module

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
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
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
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/safe/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/safe/workspace",
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
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/safe/workspace",
            a2a_allow_directory_override=False,
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/safe/workspace",
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
    import opencode_a2a_server.app as app_module

    class NotFoundPromptAsyncClient(DummyOpencodeClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-404/prompt_async")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeClient", NotFoundPromptAsyncClient)
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
    import opencode_a2a_server.app as app_module

    class InvalidPromptAsyncStatusClient(DummyOpencodeClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            raise UpstreamContractError(
                "OpenCode /session/{sessionID}/prompt_async must return 204; got 200"
            )

    monkeypatch.setattr(app_module, "OpencodeClient", InvalidPromptAsyncStatusClient)
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
    import opencode_a2a_server.app as app_module

    class UpstreamErrorPromptAsyncClient(DummyOpencodeClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/prompt_async")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("Internal Server Error", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeClient", UpstreamErrorPromptAsyncClient)
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
    import opencode_a2a_server.app as app_module

    class NetworkErrorPromptAsyncClient(DummyOpencodeClient):
        async def session_prompt_async(self, session_id: str, request: dict, *, directory=None):
            del session_id, request, directory
            req = httpx.Request("POST", "http://opencode/session/s-1/prompt_async")
            raise httpx.ConnectError("network down", request=req)

    monkeypatch.setattr(app_module, "OpencodeClient", NetworkErrorPromptAsyncClient)
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
    import opencode_a2a_server.app as app_module
    from opencode_a2a_server.agent import OpencodeAgentExecutor

    class NetworkErrorPromptAsyncClient(DummyOpencodeClient):
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
    monkeypatch.setattr(app_module, "OpencodeClient", NetworkErrorPromptAsyncClient)
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
    import opencode_a2a_server.app as app_module

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
                "method": "opencode.sessions.prompt_async",
                "params": {
                    "session_id": "s-1",
                    "request": {"parts": [{"type": "text", "text": "hello"}]},
                },
            },
        )
        assert resp.status_code == 204
        assert len(dummy.prompt_async_calls) == 1


@pytest.mark.asyncio
async def test_session_command_extension_success(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_directory="/workspace",
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
    import opencode_a2a_server.app as app_module

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
    import opencode_a2a_server.app as app_module

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


@pytest.mark.asyncio
async def test_session_command_extension_maps_404_to_session_not_found(monkeypatch):
    import opencode_a2a_server.app as app_module

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
    import opencode_a2a_server.app as app_module

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
        assert payload["error"]["code"] == -32007
        assert payload["error"]["data"]["type"] == "METHOD_DISABLED"
        assert dummy.shell_calls == []


@pytest.mark.asyncio
async def test_session_shell_extension_success_when_enabled(monkeypatch):
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            opencode_directory="/workspace",
            **_BASE_SETTINGS,
        )
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            a2a_enable_session_shell=True,
            opencode_directory="/workspace",
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
    import opencode_a2a_server.app as app_module

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
    import opencode_a2a_server.app as app_module

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
    import opencode_a2a_server.app as app_module

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
    import opencode_a2a_server.app as app_module

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


@pytest.mark.asyncio
async def test_interrupt_callback_extension_permission_reply(monkeypatch):
    import opencode_a2a_server.app as app_module

    class InterruptClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self.permission_reply_calls: list[dict] = []

        async def permission_reply(
            self,
            request_id: str,
            *,
            reply: str,
            message: str | None = None,
            directory: str | None = None,
        ) -> bool:
            self.permission_reply_calls.append(
                {
                    "request_id": request_id,
                    "reply": reply,
                    "message": message,
                    "directory": directory,
                }
            )
            return True

    dummy = InterruptClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.remember_interrupt_request(
        request_id="perm-1",
        session_id="ses-1",
        interrupt_type="permission",
        task_id="task-perm",
        context_id="ctx-perm",
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
                "id": 11,
                "method": "a2a.interrupt.permission.reply",
                "params": {
                    "request_id": "perm-1",
                    "reply": "once",
                    "message": "approved by operator",
                    "metadata": {
                        "opencode": {
                            "directory": "/workspace",
                        }
                    },
                },
            },
        )
        payload = resp.json()
        assert payload.get("error") is None
        assert payload["result"]["ok"] is True
        assert payload["result"]["request_id"] == "perm-1"
        assert set(payload["result"]) == {"ok", "request_id"}
        assert len(dummy.permission_reply_calls) == 1
        assert dummy.permission_reply_calls[0]["request_id"] == "perm-1"
        assert dummy.permission_reply_calls[0]["reply"] == "once"
        assert dummy.permission_reply_calls[0]["directory"] == "/workspace"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_legacy_permission_fields(monkeypatch):
    import opencode_a2a_server.app as app_module

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
                "id": 111,
                "method": "a2a.interrupt.permission.reply",
                "params": {"requestID": "perm-legacy", "decision": "allow"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_legacy_metadata_directory(monkeypatch):
    import opencode_a2a_server.app as app_module

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
                "id": 112,
                "method": "a2a.interrupt.permission.reply",
                "params": {
                    "request_id": "perm-legacy",
                    "reply": "once",
                    "metadata": {
                        "directory": "/workspace",
                    },
                },
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["fields"] == ["metadata.directory"]


@pytest.mark.asyncio
async def test_interrupt_callback_extension_question_reply_and_reject(monkeypatch):
    import opencode_a2a_server.app as app_module

    class InterruptClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self.question_reply_calls: list[dict] = []
            self.question_reject_calls: list[dict] = []

        async def question_reply(
            self,
            request_id: str,
            *,
            answers: list[list[str]],
            directory: str | None = None,
        ) -> bool:
            self.question_reply_calls.append(
                {"request_id": request_id, "answers": answers, "directory": directory}
            )
            return True

        async def question_reject(
            self,
            request_id: str,
            *,
            directory: str | None = None,
        ) -> bool:
            self.question_reject_calls.append({"request_id": request_id, "directory": directory})
            return True

    dummy = InterruptClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.remember_interrupt_request(
        request_id="q-1",
        session_id="ses-1",
        interrupt_type="question",
    )
    dummy.remember_interrupt_request(
        request_id="q-2",
        session_id="ses-1",
        interrupt_type="question",
    )
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        reply_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "a2a.interrupt.question.reply",
                "params": {
                    "request_id": "q-1",
                    "answers": [["A"], ["B"]],
                    "metadata": {
                        "opencode": {
                            "directory": "/workspace/question/reply",
                        }
                    },
                },
            },
        )
        reply_payload = reply_resp.json()
        assert reply_payload["result"]["ok"] is True
        assert reply_payload["result"]["request_id"] == "q-1"
        assert set(reply_payload["result"]) == {"ok", "request_id"}
        assert dummy.question_reply_calls[0]["answers"] == [["A"], ["B"]]
        assert dummy.question_reply_calls[0]["directory"] == "/workspace/question/reply"

        reject_resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "a2a.interrupt.question.reject",
                "params": {
                    "request_id": "q-2",
                    "metadata": {
                        "opencode": {
                            "directory": "/workspace/question/reject",
                        }
                    },
                },
            },
        )
        reject_payload = reject_resp.json()
        assert reject_payload["result"]["ok"] is True
        assert dummy.question_reject_calls[0]["request_id"] == "q-2"
        assert dummy.question_reject_calls[0]["directory"] == "/workspace/question/reject"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_maps_404_to_interrupt_not_found(monkeypatch):
    import opencode_a2a_server.app as app_module

    class NotFoundInterruptClient(DummyOpencodeClient):
        async def permission_reply(
            self,
            request_id: str,
            *,
            reply: str,
            message: str | None = None,
            directory: str | None = None,
        ) -> bool:
            del request_id, reply, message, directory
            request = httpx.Request("POST", "http://opencode/permission/x/reply")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    settings = make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    dummy = NotFoundInterruptClient(settings)
    dummy.remember_interrupt_request(
        request_id="perm-404",
        session_id="ses-1",
        interrupt_type="permission",
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
                "id": 14,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-404", "reply": "reject"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_expired_request(monkeypatch):
    import opencode_a2a_server.app as app_module

    class ExpiredInterruptClient(DummyOpencodeClient):
        def resolve_interrupt_request(self, request_id: str):
            del request_id
            return "expired", None

    monkeypatch.setattr(app_module, "OpencodeClient", ExpiredInterruptClient)
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
                "id": 15,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-expired", "reply": "once"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_EXPIRED"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_unknown_request_id(monkeypatch):
    import opencode_a2a_server.app as app_module

    class InterruptClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self.permission_reply_calls: list[str] = []

        async def permission_reply(
            self,
            request_id: str,
            *,
            reply: str,
            message: str | None = None,
            directory: str | None = None,
        ) -> bool:
            del reply, message, directory
            self.permission_reply_calls.append(request_id)
            return True

    dummy = InterruptClient(
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
                "id": 16,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-unknown", "reply": "once"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"
        assert dummy.permission_reply_calls == []


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_interrupt_type_mismatch(monkeypatch):
    import opencode_a2a_server.app as app_module

    class InterruptClient(DummyOpencodeClient):
        pass

    dummy = InterruptClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.remember_interrupt_request(
        request_id="q-only",
        session_id="ses-1",
        interrupt_type="question",
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
                "id": 17,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "q-only", "reply": "once"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["type"] == "INTERRUPT_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_identity_mismatch(monkeypatch):
    import opencode_a2a_server.app as app_module

    class InterruptClient(DummyOpencodeClient):
        pass

    dummy = InterruptClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.remember_interrupt_request(
        request_id="perm-owned",
        session_id="ses-1",
        interrupt_type="permission",
        identity="bearer:other-identity",
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
                "id": 18,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-owned", "reply": "once"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"

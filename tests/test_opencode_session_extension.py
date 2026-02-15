import logging

import httpx
import pytest

from opencode_a2a_serve.config import Settings
from tests.helpers import DummySessionQueryOpencodeClient as DummyOpencodeClient
from tests.helpers import make_settings

_BASE_SETTINGS = {
    "opencode_timeout": 1.0,
    "a2a_log_level": "DEBUG",
}


@pytest.mark.asyncio
async def test_session_query_extension_requires_bearer_token(monkeypatch):
    import opencode_a2a_serve.app as app_module

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
    import opencode_a2a_serve.app as app_module

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
                "id": 1,
                "method": "opencode.sessions.list",
                "params": {"page": 1, "size": 10},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
        assert "raw" not in payload["result"]
        session = payload["result"]["items"][0]
        assert session["id"] == "s-1"
        assert session["contextId"] == "s-1"
        assert session["metadata"]["opencode"]["raw"]["id"] == "s-1"
        assert session["metadata"]["opencode"]["title"] == "Untitled session"
        assert dummy.last_sessions_params is not None
        assert dummy.last_sessions_params.get("page") == 1
        assert dummy.last_sessions_params.get("size") == 10

        resp = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "opencode.sessions.messages.list",
                "params": {"session_id": "s-1", "page": 2, "size": 5},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert "raw" not in payload["result"]
        message = payload["result"]["items"][0]
        assert message["contextId"] == "s-1"
        assert message["parts"][0]["text"] == "SECRET_HISTORY"
        assert message["metadata"]["opencode"]["session_id"] == "s-1"
        assert dummy.last_messages_params is not None
        assert dummy.last_messages_params.get("page") == 2
        assert dummy.last_messages_params.get("size") == 5


@pytest.mark.asyncio
async def test_session_query_extension_items_is_always_array(monkeypatch):
    import opencode_a2a_serve.app as app_module

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
        assert payload["result"]["items"] == []


@pytest.mark.asyncio
async def test_session_query_extension_session_title_is_extracted_or_placeholder(monkeypatch):
    import opencode_a2a_serve.app as app_module

    class TitlePayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"items": [{"id": "s-1", "title": "My Session"}]}

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
        assert session["metadata"]["opencode"]["title"] == "My Session"


@pytest.mark.asyncio
async def test_session_query_extension_message_role_and_id_from_info(monkeypatch):
    import opencode_a2a_serve.app as app_module

    class InfoRoleClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._messages_payload = {
                "items": [
                    {
                        "info": {"id": "msg-1", "role": "user"},
                        "parts": [{"type": "text", "text": "hello"}],
                    }
                ]
            }

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
    import opencode_a2a_serve.app as app_module

    class ListPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1"}]
            self._messages_payload = [{"id": "m-1", "text": "SECRET_HISTORY"}]

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
        assert payload["result"]["items"][0]["contextId"] == "s-1"
        assert payload["result"]["items"][0]["parts"][0]["text"] == "SECRET_HISTORY"


@pytest.mark.asyncio
async def test_session_query_extension_accepts_alternative_list_keys(monkeypatch):
    import opencode_a2a_serve.app as app_module

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
        assert payload["result"]["items"][0]["id"] == "s-1"

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
        assert payload["result"]["items"][0]["contextId"] == "s-1"
        assert payload["result"]["items"][0]["parts"][0]["text"] == "SECRET_HISTORY"


@pytest.mark.asyncio
async def test_session_query_extension_rejects_cursor_limit(monkeypatch):
    import opencode_a2a_serve.app as app_module

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
async def test_session_query_extension_rejects_size_over_max(monkeypatch):
    import opencode_a2a_serve.app as app_module

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
    import opencode_a2a_serve.app as app_module

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
    import opencode_a2a_serve.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    caplog.set_level(logging.DEBUG, logger="opencode_a2a_serve.app")

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

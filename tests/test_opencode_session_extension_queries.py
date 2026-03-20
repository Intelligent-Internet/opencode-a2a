import logging

import httpx
import pytest

from opencode_a2a_server.config import Settings
from opencode_a2a_server.contracts.extensions import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
)
from tests.helpers import DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient
from tests.helpers import make_settings
from tests.session_extension_fixtures import _BASE_SETTINGS, _session_meta


@pytest.mark.asyncio
async def test_session_query_extension_requires_bearer_token(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyOpencodeUpstreamClient)
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
async def test_session_query_extension_applies_default_limit(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    dummy._sessions_payload = [
        {"id": f"s-{index}", "title": f"Session s-{index}"}
        for index in range(1, SESSION_QUERY_DEFAULT_LIMIT + 6)
    ]
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
            json={"jsonrpc": "2.0", "id": 1, "method": "opencode.sessions.list", "params": {}},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert len(payload["result"]["items"]) == SESSION_QUERY_DEFAULT_LIMIT
        assert dummy.last_sessions_params is not None
        assert dummy.last_sessions_params["limit"] == SESSION_QUERY_DEFAULT_LIMIT

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
        assert resp.status_code == 200
        assert dummy.last_messages_params is not None
        assert dummy.last_messages_params["limit"] == SESSION_QUERY_DEFAULT_LIMIT


@pytest.mark.asyncio
async def test_session_query_extension_enforces_session_limit_locally(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            a2a_log_payloads=False,
            opencode_workspace_root="/workspace",
            **_BASE_SETTINGS,
        )
    )
    dummy._sessions_payload = [
        {"id": "s-1", "title": "Session s-1"},
        {"id": "s-2", "title": "Session s-2"},
        {"id": "s-3", "title": "Session s-3"},
    ]
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
                "id": 3,
                "method": "opencode.sessions.list",
                "params": {"limit": 2},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert [item["id"] for item in payload["result"]["items"]] == ["s-1", "s-2"]
        assert dummy.last_sessions_params == {"limit": 2}


@pytest.mark.asyncio
async def test_provider_discovery_extension_returns_normalized_catalog(monkeypatch):
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
    import opencode_a2a_server.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.provider_catalog_payload = {"all": "bad", "default": {}, "connected": []}
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
    import opencode_a2a_server.server.application as app_module

    class WeirdPayloadClient(DummyOpencodeUpstreamClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"foo": "bar"}  # no items

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", WeirdPayloadClient)
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
    import opencode_a2a_server.server.application as app_module

    class TitlePayloadClient(DummyOpencodeUpstreamClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "My Session"}]

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", TitlePayloadClient)
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
    import opencode_a2a_server.server.application as app_module

    class EmptyTitlePayloadClient(DummyOpencodeUpstreamClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "   "}]

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", EmptyTitlePayloadClient)
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
    import opencode_a2a_server.server.application as app_module

    class InfoRoleClient(DummyOpencodeUpstreamClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._messages_payload = [
                {
                    "info": {"id": "msg-1", "role": "user"},
                    "parts": [{"type": "text", "text": "hello"}],
                }
            ]

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", InfoRoleClient)
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
    import opencode_a2a_server.server.application as app_module

    class ListPayloadClient(DummyOpencodeUpstreamClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1", "title": "s1"}]
            self._messages_payload = [
                {
                    "info": {"id": "m-1", "role": "assistant"},
                    "parts": [{"type": "text", "text": "SECRET_HISTORY"}],
                }
            ]

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", ListPayloadClient)
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
    import opencode_a2a_server.server.application as app_module

    class AltKeyPayloadClient(DummyOpencodeUpstreamClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"sessions": [{"id": "s-1"}]}
            self._messages_payload = {"messages": [{"id": "m-1", "text": "SECRET_HISTORY"}]}

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", AltKeyPayloadClient)
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
async def test_session_query_extension_rejects_limit_above_max(monkeypatch):
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
                "id": 3,
                "method": "opencode.sessions.list",
                "params": {"limit": SESSION_QUERY_MAX_LIMIT + 1},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 3
        assert payload["error"]["code"] == -32602
        assert payload["error"]["data"]["field"] == "limit"
        assert payload["error"]["data"]["max"] == SESSION_QUERY_MAX_LIMIT


@pytest.mark.asyncio
async def test_session_query_extension_accepts_equivalent_string_and_integer_limit(
    monkeypatch,
):
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
                "id": 4,
                "method": "opencode.sessions.list",
                "params": {"limit": "2", "query": {"limit": 2}},
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 4
        assert "error" not in payload
        assert dummy.last_sessions_params == {"limit": 2}


@pytest.mark.asyncio
async def test_session_query_extension_maps_404_to_session_not_found(monkeypatch):
    import opencode_a2a_server.server.application as app_module

    class NotFoundOpencodeUpstreamClient(DummyOpencodeUpstreamClient):
        async def list_messages(self, session_id: str, *, params=None):
            request = httpx.Request("GET", "http://opencode/session/x/message")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NotFoundOpencodeUpstreamClient)
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
    import opencode_a2a_server.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyOpencodeUpstreamClient)
    caplog.set_level(logging.DEBUG, logger="opencode_a2a_server.server.application")

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

import logging
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from opencode_a2a.config import Settings

TEST_JWT_ISSUER = "test-issuer"
TEST_JWT_AUDIENCE = "test-audience"


def _generate_rsa_keypair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


_PRIVATE_KEY_PEM, _PUBLIC_KEY_PEM = _generate_rsa_keypair()


def _jwt_token() -> str:
    return jwt.encode(
        {
            "iss": TEST_JWT_ISSUER,
            "aud": TEST_JWT_AUDIENCE,
            "exp": int(time.time()) + 3600,
        },
        _PRIVATE_KEY_PEM,
        algorithm="RS256",
    )


class DummyOpencodeClient:
    def __init__(self, _settings: Settings) -> None:
        self._sessions_payload = {"items": [{"id": "s-1"}]}
        self._messages_payload = {"items": [{"id": "m-1", "text": "SECRET_HISTORY"}]}
        self.last_sessions_params = None
        self.last_messages_params = None

    async def close(self) -> None:
        return None

    async def list_sessions(self, *, params=None):
        self.last_sessions_params = params
        return self._sessions_payload

    async def list_messages(self, session_id: str, *, params=None):
        assert session_id
        self.last_messages_params = params
        return self._messages_payload


def _settings(*, log_payloads: bool) -> Settings:
    return Settings(
        opencode_base_url="http://127.0.0.1:4096",
        opencode_directory=None,
        opencode_provider_id=None,
        opencode_model_id=None,
        opencode_agent=None,
        opencode_system=None,
        opencode_variant=None,
        opencode_timeout=1.0,
        opencode_timeout_stream=None,
        a2a_public_url="http://127.0.0.1:8000",
        a2a_title="OpenCode A2A",
        a2a_description="A2A wrapper service for OpenCode",
        a2a_version="0.1.0",
        a2a_protocol_version="0.3.0",
        a2a_streaming=True,
        a2a_log_level="DEBUG",
        a2a_log_payloads=log_payloads,
        a2a_log_body_limit=0,
        a2a_documentation_url=None,
        a2a_host="127.0.0.1",
        a2a_port=8000,
        a2a_jwt_secret=_PUBLIC_KEY_PEM,
        a2a_jwt_algorithm="RS256",
        a2a_jwt_issuer=TEST_JWT_ISSUER,
        a2a_jwt_audience=TEST_JWT_AUDIENCE,
        a2a_jwt_scope_match="any",
        a2a_required_scopes=set(),
        a2a_session_cache_ttl_seconds=3600,
        a2a_session_cache_maxsize=10_000,
    )


@pytest.mark.asyncio
async def test_session_query_extension_requires_bearer_token(monkeypatch):
    import opencode_a2a.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    app = app_module.create_app(_settings(log_payloads=False))

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
    import opencode_a2a.app as app_module

    dummy = DummyOpencodeClient(_settings(log_payloads=False))
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    class WeirdPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"foo": "bar"}  # no items

    monkeypatch.setattr(app_module, "OpencodeClient", WeirdPayloadClient)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    class TitlePayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"items": [{"id": "s-1", "title": "My Session"}]}

    monkeypatch.setattr(app_module, "OpencodeClient", TitlePayloadClient)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

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
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    class ListPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = [{"id": "s-1"}]
            self._messages_payload = [{"id": "m-1", "text": "SECRET_HISTORY"}]

    monkeypatch.setattr(app_module, "OpencodeClient", ListPayloadClient)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    class AltKeyPayloadClient(DummyOpencodeClient):
        def __init__(self, _settings: Settings) -> None:
            super().__init__(_settings)
            self._sessions_payload = {"sessions": [{"id": "s-1"}]}
            self._messages_payload = {"messages": [{"id": "m-1", "text": "SECRET_HISTORY"}]}

    monkeypatch.setattr(app_module, "OpencodeClient", AltKeyPayloadClient)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    dummy = DummyOpencodeClient(_settings(log_payloads=False))
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    dummy = DummyOpencodeClient(_settings(log_payloads=False))
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    class NotFoundOpencodeClient(DummyOpencodeClient):
        async def list_messages(self, session_id: str, *, params=None):
            request = httpx.Request("GET", "http://opencode/session/x/message")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    monkeypatch.setattr(app_module, "OpencodeClient", NotFoundOpencodeClient)
    app = app_module.create_app(_settings(log_payloads=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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
    import opencode_a2a.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    caplog.set_level(logging.DEBUG, logger="opencode_a2a.app")

    app = app_module.create_app(_settings(log_payloads=True))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": f"Bearer {_jwt_token()}"}
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

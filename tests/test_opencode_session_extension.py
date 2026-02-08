import logging

import httpx
import pytest

from opencode_a2a.config import Settings


class DummyOpencodeClient:
    def __init__(self, _settings: Settings) -> None:
        self._sessions_payload = {"items": [{"id": "s-1"}]}
        self._messages_payload = {"items": [{"id": "m-1", "text": "SECRET_HISTORY"}]}

    async def close(self) -> None:
        return None

    async def list_sessions(self, *, params=None):
        return self._sessions_payload

    async def list_messages(self, session_id: str, *, params=None):
        assert session_id
        return self._messages_payload


def _settings(*, token: str, log_payloads: bool) -> Settings:
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
        a2a_bearer_token=token,
        a2a_oauth_authorization_url=None,
        a2a_oauth_token_url=None,
        a2a_oauth_metadata_url=None,
        a2a_oauth_scopes={},
    )


@pytest.mark.asyncio
async def test_session_query_extension_requires_bearer_token(monkeypatch):
    import opencode_a2a.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    app = app_module.create_app(_settings(token="t-1", log_payloads=False))

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

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    app = app_module.create_app(_settings(token="t-1", log_payloads=False))

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
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 1
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
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        assert payload["result"]["items"][0]["text"] == "SECRET_HISTORY"


@pytest.mark.asyncio
async def test_session_query_extension_does_not_log_response_bodies(monkeypatch, caplog):
    import opencode_a2a.app as app_module

    monkeypatch.setattr(app_module, "OpencodeClient", DummyOpencodeClient)
    caplog.set_level(logging.DEBUG, logger="opencode_a2a.app")

    app = app_module.create_app(_settings(token="t-1", log_payloads=True))

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

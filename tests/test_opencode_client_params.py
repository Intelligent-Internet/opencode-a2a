import pytest

from opencode_a2a.config import Settings
from opencode_a2a.opencode_client import OpencodeClient


def _settings(*, directory: str | None) -> Settings:
    return Settings(
        opencode_base_url="http://127.0.0.1:4096",
        opencode_directory=directory,
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
        a2a_log_payloads=False,
        a2a_log_body_limit=0,
        a2a_documentation_url=None,
        a2a_host="127.0.0.1",
        a2a_port=8000,
        a2a_bearer_token="t-1",
        a2a_oauth_authorization_url=None,
        a2a_oauth_token_url=None,
        a2a_oauth_metadata_url=None,
        a2a_oauth_scopes={},
        a2a_session_cache_ttl_seconds=3600,
        a2a_session_cache_maxsize=10_000,
    )


class _DummyResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"ok": True}


@pytest.mark.asyncio
async def test_merge_params_does_not_allow_directory_override(monkeypatch):
    client = OpencodeClient(_settings(directory="/safe"))

    seen = {}

    async def fake_get(path: str, *, params=None, **_kwargs):
        seen["path"] = path
        seen["params"] = params
        return _DummyResponse()

    monkeypatch.setattr(client._client, "get", fake_get)

    await client.list_sessions(params={"directory": "/evil", "page": 1})
    assert seen["path"] == "/session"
    assert seen["params"]["directory"] == "/safe"
    assert seen["params"]["page"] == "1"

    await client.list_messages("sess-1", params={"directory": "/evil", "size": 10})
    assert seen["path"] == "/session/sess-1/message"
    assert seen["params"]["directory"] == "/safe"
    assert seen["params"]["size"] == "10"

    await client.close()

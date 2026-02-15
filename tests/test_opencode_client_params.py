import pytest

from opencode_a2a_serve.opencode_client import OpencodeClient
from tests.helpers import make_settings


class _DummyResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"ok": True}


@pytest.mark.asyncio
async def test_merge_params_does_not_allow_directory_override(monkeypatch):
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_directory="/safe",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

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

import httpx
import pytest

from opencode_a2a_serve.opencode_client import OpencodeClient
from tests.helpers import make_settings


class _DummyResponse:
    def __init__(self, payload=None) -> None:
        self._payload = {"ok": True} if payload is None else payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


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

    await client.list_sessions(params={"directory": "/evil", "limit": 1, "roots": True})
    assert seen["path"] == "/session"
    assert seen["params"]["directory"] == "/safe"
    assert seen["params"]["limit"] == "1"
    assert seen["params"]["roots"] == "True"

    await client.list_messages("sess-1", params={"directory": "/evil", "limit": 10})
    assert seen["path"] == "/session/sess-1/message"
    assert seen["params"]["directory"] == "/safe"
    assert seen["params"]["limit"] == "10"

    await client.close()


@pytest.mark.asyncio
async def test_permission_reply_raises_on_404_without_legacy_fallback(monkeypatch):
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_directory="/safe",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    calls: list[tuple[str, dict | None]] = []

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        calls.append((path, json))
        request = httpx.Request("POST", f"http://opencode{path}")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("Not Found", request=request, response=response)

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        await client.permission_reply(
            "perm-1",
            reply="once",
        )
    assert calls[0][0] == "/permission/perm-1/reply"
    assert calls[0][1] == {"reply": "once"}
    assert len(calls) == 1

    await client.close()


@pytest.mark.asyncio
async def test_question_reply_posts_answers(monkeypatch):
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    seen = {}

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        seen["path"] = path
        seen["params"] = params
        seen["json"] = json
        return _DummyResponse(True)

    monkeypatch.setattr(client._client, "post", fake_post)

    ok = await client.question_reply("q-1", answers=[["A"], ["B"]])
    assert ok is True
    assert seen["path"] == "/question/q-1/reply"
    assert seen["json"] == {"answers": [["A"], ["B"]]}

    await client.close()


@pytest.mark.asyncio
async def test_permission_reply_rejects_non_boolean_payload(monkeypatch):
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        del path, params, json
        return _DummyResponse({"ok": True})

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(RuntimeError, match="response must be boolean"):
        await client.permission_reply("perm-1", reply="once")

    await client.close()


@pytest.mark.asyncio
async def test_question_reject_rejects_non_boolean_payload(monkeypatch):
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        del path, params, json
        return _DummyResponse({"ok": True})

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(RuntimeError, match="response must be boolean"):
        await client.question_reject("q-1")

    await client.close()


@pytest.mark.asyncio
async def test_interrupt_request_binding_expires_after_ttl() -> None:
    client = OpencodeClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    now = 1000.0
    client._interrupt_request_clock = lambda: now  # type: ignore[method-assign]
    client.remember_interrupt_request(
        request_id="perm-1",
        session_id="ses-1",
        interrupt_type="permission",
        task_id="task-1",
        context_id="ctx-1",
        identity="user-1",
        ttl_seconds=5.0,
    )

    status, binding = client.resolve_interrupt_request("perm-1")
    assert status == "active"
    assert binding is not None
    assert binding.session_id == "ses-1"
    assert binding.interrupt_type == "permission"

    now = 1006.0
    status, binding = client.resolve_interrupt_request("perm-1")
    assert status == "expired"
    assert binding is None
    assert client.resolve_interrupt_session("perm-1") is None

    await client.close()

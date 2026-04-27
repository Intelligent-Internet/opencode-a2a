from pathlib import Path

import httpx
import pytest

from tests.support.helpers import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.helpers import make_settings
from tests.support.jsonrpc_error_assertions import assert_v1_error_reason
from tests.support.session_extensions import _BASE_SETTINGS, _jsonrpc_app, _session_meta


def _identity_for_token(token: str) -> str:
    del token
    return "automation"


@pytest.mark.asyncio
async def test_session_lifecycle_status_get_and_children_success(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        status_response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 401,
                "method": "opencode.sessions.status",
                "params": {"directory": "services/api"},
            },
        )
        get_response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 402,
                "method": "opencode.sessions.get",
                "params": {"session_id": "s-1"},
            },
        )
        children_response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 403,
                "method": "opencode.sessions.children",
                "params": {"session_id": "s-1"},
            },
        )

    assert status_response.status_code == 200
    status_payload = status_response.json()["result"]["items"]
    assert status_payload == [
        {"session_id": "s-1", "type": "idle"},
        {
            "session_id": "s-2",
            "type": "retry",
            "attempt": 2,
            "message": "retrying",
            "next": 30,
        },
    ]
    assert dummy.lifecycle_calls[0]["directory"].endswith("/services/api")

    assert get_response.status_code == 200
    get_item = get_response.json()["result"]["item"]
    assert get_item["id"] == "s-1"
    assert _session_meta(get_item)["title"] == "Session s-1"

    assert children_response.status_code == 200
    children_items = children_response.json()["result"]["items"]
    assert [item["id"] for item in children_items] == ["s-2"]


@pytest.mark.asyncio
async def test_session_lifecycle_todo_diff_and_message_get_success(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"Authorization": "Bearer t-1"}
        todo_response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 404,
                "method": "opencode.sessions.todo",
                "params": {"session_id": "s-1"},
            },
        )
        diff_response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 405,
                "method": "opencode.sessions.diff",
                "params": {"session_id": "s-1", "message_id": "msg-1"},
            },
        )
        message_response = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 406,
                "method": "opencode.sessions.messages.get",
                "params": {"session_id": "s-1", "message_id": "m-1"},
            },
        )

    assert todo_response.json()["result"]["items"] == [
        {
            "id": "todo-1",
            "content": "Review the diff",
            "status": "pending",
            "priority": "high",
        }
    ]
    assert diff_response.json()["result"]["items"] == [
        {
            "file": "src/app.py",
            "before": "old",
            "after": "new",
            "additions": 3,
            "deletions": 1,
        }
    ]
    assert dummy.lifecycle_calls[1]["params"] == {"messageID": "msg-1"}

    message_item = message_response.json()["result"]["item"]
    assert message_item["messageId"] == "m-1"
    assert message_item["parts"][0]["text"] == "One message payload"
    assert _session_meta(message_item)["id"] == "s-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "expected_method", "expected_id"),
    [
        ("opencode.sessions.fork", "fork_session", "s-2"),
        ("opencode.sessions.share", "share_session", "s-1"),
        ("opencode.sessions.unshare", "unshare_session", "s-1"),
        ("opencode.sessions.revert", "revert_session", "s-1"),
        ("opencode.sessions.unrevert", "unrevert_session", "s-1"),
    ],
)
async def test_session_lifecycle_mutations_succeed_and_claim_owner(
    monkeypatch,
    method: str,
    expected_method: str,
    expected_id: str,
):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    params = {"session_id": "s-1"}
    if method == "opencode.sessions.fork":
        params["request"] = {"messageID": "msg-1"}
    elif method == "opencode.sessions.revert":
        params["request"] = {"messageID": "msg-1", "partID": "part-1"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={"jsonrpc": "2.0", "id": 407, "method": method, "params": params},
        )

    assert response.status_code == 200
    payload = response.json()["result"]["item"]
    assert payload["id"] == expected_id
    assert dummy.lifecycle_calls[0]["method"] == expected_method
    if method == "opencode.sessions.revert":
        assert payload["revert"] == {
            "message_id": "msg-1",
            "part_id": "part-1",
            "snapshot": "snap-1",
            "diff": "diff-1",
        }
    if method == "opencode.sessions.unrevert":
        assert "revert" not in payload

    claim_result = await _jsonrpc_app(app)._session_claim(  # noqa: SLF001
        identity=_identity_for_token("t-1"),
        session_id=expected_id,
    )
    assert claim_result is False


@pytest.mark.asyncio
async def test_session_lifecycle_summarize_succeeds_and_claims_owner(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 4071,
                "method": "opencode.sessions.summarize",
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "providerID": "openai",
                        "modelID": "gpt-5",
                        "auto": True,
                    },
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == {"ok": True, "session_id": "s-1"}
    assert dummy.lifecycle_calls[0]["method"] == "summarize_session"
    assert dummy.lifecycle_calls[0]["session_id"] == "s-1"
    assert dummy.lifecycle_calls[0]["request"] == {
        "providerID": "openai",
        "modelID": "gpt-5",
        "auto": True,
    }
    assert Path(dummy.lifecycle_calls[0]["directory"]).resolve() == Path.cwd().resolve()
    assert dummy.lifecycle_calls[0]["workspace_id"] is None

    claim_result = await _jsonrpc_app(app)._session_claim(  # noqa: SLF001
        identity=_identity_for_token("t-1"),
        session_id="s-1",
    )
    assert claim_result is False


@pytest.mark.asyncio
async def test_session_lifecycle_mutation_rejects_owner_mismatch(monkeypatch):
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    await _jsonrpc_app(app)._session_claim_finalize(  # noqa: SLF001
        identity="other-user",
        session_id="s-1",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 408,
                "method": "opencode.sessions.share",
                "params": {"session_id": "s-1"},
            },
        )

    payload = response.json()
    assert payload["error"]["code"] == -32006
    assert_v1_error_reason(
        payload["error"],
        reason="SESSION_FORBIDDEN",
        metadata={"session_id": "s-1"},
    )
    assert dummy.lifecycle_calls == []


@pytest.mark.asyncio
async def test_session_lifecycle_rejects_invalid_params_and_maps_404(monkeypatch):
    import opencode_a2a.server.application as app_module

    class NotFoundLifecycleClient(DummyOpencodeUpstreamClient):
        async def get_session(self, session_id: str, *, directory=None, workspace_id=None):
            del session_id, directory, workspace_id
            req = httpx.Request("GET", "http://opencode/session/s-404")
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("Not Found", request=req, response=resp)

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", NotFoundLifecycleClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_message_id = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 409,
                "method": "opencode.sessions.messages.get",
                "params": {"session_id": "s-1"},
            },
        )
        invalid_summarize = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 4091,
                "method": "opencode.sessions.summarize",
                "params": {
                    "session_id": "s-1",
                    "request": {"providerID": "openai", "modelID": "gpt-5", "auto": "yes"},
                },
            },
        )
        missing_revert_message_id = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 4092,
                "method": "opencode.sessions.revert",
                "params": {"session_id": "s-1", "request": {}},
            },
        )
        not_found = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={
                "jsonrpc": "2.0",
                "id": 410,
                "method": "opencode.sessions.get",
                "params": {"session_id": "s-404"},
            },
        )

    assert missing_message_id.json()["error"]["data"]["field"] == "message_id"
    assert invalid_summarize.json()["error"]["data"]["field"] == "request.auto"
    assert missing_revert_message_id.json()["error"]["data"]["field"] == "request.messageID"
    assert not_found.json()["error"]["code"] == -32001
    assert_v1_error_reason(
        not_found.json()["error"],
        reason="SESSION_NOT_FOUND",
        metadata={"session_id": "s-404"},
    )

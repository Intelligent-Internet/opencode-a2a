import httpx
import pytest

from opencode_a2a.config import Settings
from tests.support.helpers import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.helpers import make_settings
from tests.support.session_extensions import _BASE_SETTINGS


@pytest.mark.asyncio
async def test_interrupt_callback_extension_permission_reply(monkeypatch):
    import opencode_a2a.server.application as app_module

    class InterruptClient(DummyOpencodeUpstreamClient):
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
    import opencode_a2a.server.application as app_module

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
                "id": 111,
                "method": "a2a.interrupt.permission.reply",
                "params": {"requestID": "perm-legacy", "decision": "allow"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_legacy_metadata_directory(monkeypatch):
    import opencode_a2a.server.application as app_module

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
    import opencode_a2a.server.application as app_module

    class InterruptClient(DummyOpencodeUpstreamClient):
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
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
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
    import opencode_a2a.server.application as app_module

    class NotFoundInterruptClient(DummyOpencodeUpstreamClient):
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
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-404", "reply": "reject"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"


@pytest.mark.asyncio
async def test_interrupt_callback_extension_rejects_expired_request(monkeypatch):
    import opencode_a2a.server.application as app_module

    class ExpiredInterruptClient(DummyOpencodeUpstreamClient):
        def resolve_interrupt_request(self, request_id: str):
            del request_id
            return "expired", None

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", ExpiredInterruptClient)
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
    import opencode_a2a.server.application as app_module

    class InterruptClient(DummyOpencodeUpstreamClient):
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
    import opencode_a2a.server.application as app_module

    class InterruptClient(DummyOpencodeUpstreamClient):
        pass

    dummy = InterruptClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False, **_BASE_SETTINGS)
    )
    dummy.remember_interrupt_request(
        request_id="q-only",
        session_id="ses-1",
        interrupt_type="question",
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
    import opencode_a2a.server.application as app_module

    class InterruptClient(DummyOpencodeUpstreamClient):
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
                "id": 18,
                "method": "a2a.interrupt.permission.reply",
                "params": {"request_id": "perm-owned", "reply": "once"},
            },
        )
        payload = resp.json()
        assert payload["error"]["code"] == -32004
        assert payload["error"]["data"]["type"] == "INTERRUPT_REQUEST_NOT_FOUND"

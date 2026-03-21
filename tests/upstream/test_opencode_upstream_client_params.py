import json as json_module

import httpx
import pytest

from opencode_a2a_server.opencode_upstream_client import (
    _UNSET,
    OpencodeUpstreamClient,
    UpstreamContractError,
)
from tests.support.helpers import make_settings


class _DummyResponse:
    def __init__(
        self,
        payload=None,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self._payload = {"ok": True} if payload is None else payload
        self.status_code = status_code
        self.headers = {} if headers is None else headers
        self.text = text
        self._json_error = json_error

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


@pytest.mark.asyncio
async def test_merge_params_does_not_allow_directory_override(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
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
async def test_session_prompt_async_posts_prompt_async_endpoint(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
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
        return _DummyResponse(status_code=204)

    monkeypatch.setattr(client._client, "post", fake_post)

    payload = {
        "parts": [{"type": "text", "text": "continue"}],
        "agent": "code-reviewer",
        "noReply": True,
    }
    await client.session_prompt_async("ses-1", payload)

    assert seen["path"] == "/session/ses-1/prompt_async"
    assert seen["params"]["directory"] == "/safe"
    assert seen["json"] == payload

    await client.close()


@pytest.mark.asyncio
async def test_session_prompt_async_rejects_non_204_response(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        del path, params, json
        return _DummyResponse(status_code=200)

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(UpstreamContractError, match="must return 204"):
        await client.session_prompt_async("ses-1", {"parts": [{"type": "text", "text": "x"}]})

    await client.close()


@pytest.mark.asyncio
async def test_session_command_posts_command_endpoint(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
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
        return _DummyResponse({"info": {"id": "m-1", "role": "assistant"}, "parts": []})

    monkeypatch.setattr(client._client, "post", fake_post)

    payload = {"command": "/review", "arguments": "security"}
    data = await client.session_command("ses-1", payload)
    assert data["info"]["id"] == "m-1"
    assert seen["path"] == "/session/ses-1/command"
    assert seen["params"]["directory"] == "/safe"
    assert seen["json"] == payload

    await client.close()


@pytest.mark.asyncio
async def test_session_shell_posts_shell_endpoint(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
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
        return _DummyResponse({"id": "m-1", "role": "assistant", "parts": []})

    monkeypatch.setattr(client._client, "post", fake_post)

    payload = {"agent": "code-reviewer", "command": "git status --short"}
    data = await client.session_shell("ses-1", payload)
    assert data["id"] == "m-1"
    assert seen["path"] == "/session/ses-1/shell"
    assert seen["params"]["directory"] == "/safe"
    assert seen["json"] == payload

    await client.close()


@pytest.mark.asyncio
async def test_send_message_prefers_request_model_override(monkeypatch):
    client = OpencodeUpstreamClient(
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
        return _DummyResponse({"info": {"id": "m-1"}, "parts": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr(client._client, "post", fake_post)

    await client.send_message(
        "ses-1",
        "hello",
        model_override={"providerID": "google", "modelID": "gemini-2.5-flash"},
    )

    assert seen["path"] == "/session/ses-1/message"
    assert seen["json"]["model"] == {
        "providerID": "google",
        "modelID": "gemini-2.5-flash",
    }

    await client.close()


@pytest.mark.asyncio
async def test_send_message_ignores_partial_request_model_override(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    seen = {}

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        seen["json"] = json
        return _DummyResponse({"info": {"id": "m-1"}, "parts": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr(client._client, "post", fake_post)

    await client.send_message(
        "ses-1",
        "hello",
        model_override={"providerID": "google"},
    )

    assert "model" not in seen["json"]

    await client.close()


@pytest.mark.asyncio
async def test_send_message_accepts_structured_parts(monkeypatch):
    client = OpencodeUpstreamClient(
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
        return _DummyResponse({"info": {"id": "m-1"}, "parts": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr(client._client, "post", fake_post)

    await client.send_message(
        "ses-1",
        parts=[
            {"type": "text", "text": "describe"},
            {
                "type": "file",
                "url": "file:///tmp/report.pdf",
                "mime": "application/pdf",
                "filename": "report.pdf",
            },
        ],
    )

    assert seen["path"] == "/session/ses-1/message"
    assert seen["json"]["parts"] == [
        {"type": "text", "text": "describe"},
        {
            "type": "file",
            "url": "file:///tmp/report.pdf",
            "mime": "application/pdf",
            "filename": "report.pdf",
        },
    ]

    await client.close()


@pytest.mark.asyncio
async def test_send_message_raises_upstream_contract_error_for_non_json_response(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        del path, params, json
        return _DummyResponse(
            status_code=200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="ProviderModelNotFoundError: google/gemini-3-flash-preview",
            json_error=json_module.JSONDecodeError("Expecting value", "", 0),
        )

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(UpstreamContractError, match="returned non-JSON response") as exc_info:
        await client.send_message("ses-1", "hello")

    message = str(exc_info.value)
    assert "status=200" in message
    assert "content-type=text/plain" in message
    assert "ProviderModelNotFoundError" in message

    await client.close()


@pytest.mark.asyncio
async def test_permission_reply_raises_on_404_without_legacy_fallback(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
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
    client = OpencodeUpstreamClient(
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
    client = OpencodeUpstreamClient(
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
    client = OpencodeUpstreamClient(
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
async def test_abort_session_posts_abort_endpoint(monkeypatch):
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
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

    ok = await client.abort_session("ses-1")
    assert ok is True
    assert seen["path"] == "/session/ses-1/abort"
    assert seen["params"]["directory"] == "/safe"
    assert seen["json"] is None

    await client.close()


@pytest.mark.asyncio
async def test_abort_session_rejects_non_boolean_payload(monkeypatch):
    client = OpencodeUpstreamClient(
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
        await client.abort_session("ses-1")

    await client.close()


@pytest.mark.asyncio
async def test_interrupt_request_binding_expires_after_ttl() -> None:
    client = OpencodeUpstreamClient(
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


def test_response_body_preview_handles_empty_and_long_payloads() -> None:
    empty = _DummyResponse(text="   ")
    assert OpencodeUpstreamClient._response_body_preview(empty) == "<empty>"

    long_response = _DummyResponse(text="  " + ("word " * 60))
    preview = OpencodeUpstreamClient._response_body_preview(long_response, limit=40)
    assert preview.endswith("...")
    assert len(preview) == 40


def test_decode_json_response_reports_unknown_content_type_for_empty_body() -> None:
    response = _DummyResponse(
        text="",
        json_error=json_module.JSONDecodeError("Expecting value", "", 0),
    )

    with pytest.raises(UpstreamContractError) as exc_info:
        OpencodeUpstreamClient(make_settings(a2a_bearer_token="t-1"))._decode_json_response(
            response,
            endpoint="/session",
        )

    message = str(exc_info.value)
    assert "content-type=unknown" in message
    assert "body=<empty>" in message


def test_normalize_model_ref_rejects_blank_or_partial_values() -> None:
    assert OpencodeUpstreamClient._normalize_model_ref(None) is None
    assert OpencodeUpstreamClient._normalize_model_ref(
        {"providerID": " google ", "modelID": " gemini "}
    ) == {
        "providerID": "google",
        "modelID": "gemini",
    }
    assert OpencodeUpstreamClient._normalize_model_ref({"providerID": "google"}) is None
    assert (
        OpencodeUpstreamClient._normalize_model_ref({"providerID": "", "modelID": "gemini"}) is None
    )
    assert (
        OpencodeUpstreamClient._normalize_model_ref({"providerID": "google", "modelID": 1}) is None
    )


def test_merge_params_keeps_empty_directory_out_of_query() -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root=None,
        )
    )

    assert client._query_params() == {}
    assert client._merge_params({"limit": 5, "enabled": False}, directory=None) == {
        "limit": "5",
        "enabled": "False",
    }


@pytest.mark.asyncio
async def test_create_session_raises_when_upstream_omits_id(monkeypatch) -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        del path, params, json
        return _DummyResponse({"title": "missing id"})

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(RuntimeError, match="missing id"):
        await client.create_session("title")

    await client.close()


@pytest.mark.asyncio
async def test_create_session_raises_when_upstream_id_is_not_string(monkeypatch) -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    async def fake_post(path: str, *, params=None, json=None, **_kwargs):
        del path, params, json
        return _DummyResponse({"id": 123})

    monkeypatch.setattr(client._client, "post", fake_post)

    with pytest.raises(RuntimeError, match="missing id"):
        await client.create_session("title")

    await client.close()


@pytest.mark.asyncio
async def test_list_provider_catalog_uses_directory_query(monkeypatch) -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_workspace_root="/safe",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    seen = {}

    async def fake_get(path: str, *, params=None, **_kwargs):
        seen["path"] = path
        seen["params"] = params
        return _DummyResponse({"all": [], "default": {}, "connected": []})

    monkeypatch.setattr(client._client, "get", fake_get)

    await client.list_provider_catalog()

    assert seen["path"] == "/provider"
    assert seen["params"] == {"directory": "/safe"}

    await client.close()


@pytest.mark.asyncio
async def test_send_message_requires_text_or_parts() -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    with pytest.raises(ValueError, match="requires either text or parts"):
        await client.send_message("ses-1", None)

    with pytest.raises(ValueError, match="must not be empty"):
        await client.send_message("ses-1", parts=[])

    await client.close()


@pytest.mark.asyncio
async def test_send_message_includes_client_level_agent_system_variant(monkeypatch) -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_agent="planner",
            opencode_system="be precise",
            opencode_variant="fast",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    seen = {}

    async def fake_post(path: str, *, params=None, json=None, timeout=_UNSET, **_kwargs):
        seen["path"] = path
        seen["params"] = params
        seen["json"] = json
        seen["timeout"] = timeout
        return _DummyResponse({"info": {"id": "m-1"}, "parts": [{"type": "text", "text": "ok"}]})

    monkeypatch.setattr(client._client, "post", fake_post)

    message = await client.send_message("ses-1", "hello", timeout_override=3.5)

    assert message.message_id == "m-1"
    assert seen["path"] == "/session/ses-1/message"
    assert seen["json"]["agent"] == "planner"
    assert seen["json"]["system"] == "be precise"
    assert seen["json"]["variant"] == "fast"
    assert seen["timeout"] == 3.5

    await client.close()


@pytest.mark.asyncio
async def test_interrupt_request_helpers_ignore_invalid_and_trim_values() -> None:
    client = OpencodeUpstreamClient(
        make_settings(
            a2a_bearer_token="t-1",
            opencode_timeout=1.0,
            a2a_log_level="DEBUG",
            a2a_log_payloads=False,
        )
    )

    client.remember_interrupt_request(
        request_id="   ",
        session_id="ses-1",
        interrupt_type="permission",
    )
    client.remember_interrupt_request(
        request_id="perm-1",
        session_id="   ",
        interrupt_type="permission",
    )
    client.remember_interrupt_request(
        request_id="perm-2",
        session_id="ses-2",
        interrupt_type="unsupported",
    )

    assert client.resolve_interrupt_request("perm-1") == ("missing", None)

    client.remember_interrupt_request(
        request_id=" perm-3 ",
        session_id=" ses-3 ",
        interrupt_type=" question ",
        identity=" user-1 ",
        task_id=" task-1 ",
        context_id=" ctx-1 ",
    )
    status, binding = client.resolve_interrupt_request("perm-3")
    assert status == "active"
    assert binding is not None
    assert binding.request_id == "perm-3"
    assert binding.session_id == "ses-3"
    assert binding.identity == "user-1"
    assert binding.task_id == "task-1"
    assert binding.context_id == "ctx-1"

    assert client.resolve_interrupt_request("   ") == ("missing", None)
    client.discard_interrupt_request("   ")
    client.discard_interrupt_request("perm-3")
    assert client.resolve_interrupt_session("perm-3") is None

    await client.close()

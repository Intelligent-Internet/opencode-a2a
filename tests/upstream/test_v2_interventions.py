from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from copy import deepcopy

import httpx
import pytest
import pytest_asyncio

from opencode_a2a.opencode_upstream_client import UpstreamContractError
from opencode_a2a.upstream.v2 import V2UpstreamAdapter
from opencode_a2a.upstream.v2_interventions import V2Interventions
from tests.support.settings import make_settings


@pytest_asyncio.fixture
async def adapter(tmp_path) -> AsyncIterator[tuple[V2UpstreamAdapter, V2Interventions, list]]:
    client = V2UpstreamAdapter(make_settings(opencode_workspace_root=str(tmp_path)))
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/info":
            return httpx.Response(200, json={"version": "2.0.28"})
        return httpx.Response(204)

    client._client._transport = httpx.MockTransport(respond)
    try:
        yield client, V2Interventions(client), requests
    finally:
        await client.close()


def form(request: str = "frm_1", session: str = "ses_1", **field: object) -> dict:
    return {
        "type": "form.created",
        "properties": {
            "form": {
                "id": request,
                "sessionID": session,
                "title": "Questions",
                "metadata": {"kind": "question", "tool": {"messageID": "msg_1", "id": "call_1"}},
                "fields": [{"key": "q0", "type": "string", "title": "Name", **field}],
            }
        },
    }


def permission(request: str = "per_1", session: str = "ses_1") -> dict:
    return {
        "type": "permission.asked",
        "properties": {
            "id": request,
            "sessionID": session,
            "action": "read",
            "resources": ["file.txt"],
            "source": {"type": "tool", "messageID": "msg_1", "id": "call_1"},
        },
    }


async def bind(
    client: V2UpstreamAdapter,
    request: str = "frm_1",
    session: str = "ses_1",
    kind: str = "question",
    **kwargs,
) -> None:
    await client.remember_interrupt_request(
        request_id=request,
        session_id=session,
        interrupt_type=kind,
        identity="principal",
        credential_id="credential",
        task_id="task",
        context_id="context",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_required_typed_string_form_uses_native_answer_and_204(adapter) -> None:
    client, helper, requests = adapter
    normalized = helper.normalize_event(form(required=True), session_id="ses_1")
    assert normalized["type"] == "question.asked"
    assert normalized["properties"]["tool"] == {"messageID": "msg_1", "callID": "call_1"}
    await bind(client)
    assert await helper.question_reply("frm_1", answers=[["typed answer"]]) is True
    assert requests[-1].url.path == "/api/session/ses_1/form/frm_1/reply"
    assert json.loads(requests[-1].content) == {"answer": {"q0": "typed answer"}}
    assert (await client.resolve_interrupt_request("frm_1"))[0] == "missing"
    assert not client.prompt_execution_supported


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["once", "reject"])
async def test_permission_native_decision_and_source(adapter, reply) -> None:
    client, helper, requests = adapter
    event = helper.normalize_event(permission(), session_id="ses_1")
    assert event["properties"]["permission"] == "read"
    assert event["properties"]["patterns"] == ["file.txt"]
    await bind(client, "per_1", kind="permission")
    assert await helper.permission_reply("per_1", reply=reply, message="reason") is True
    assert requests[-1].url.path == "/api/session/ses_1/permission/per_1/reply"
    assert json.loads(requests[-1].content) == {"decision": reply, "message": "reason"}


@pytest.mark.asyncio
async def test_always_rejected_before_http(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(permission(), session_id="ses_1")
    await bind(client, "per_1", kind="permission")
    with pytest.raises(ValueError, match="only once or reject"):
        await helper.permission_reply("per_1", reply="always")
    assert requests == []
    assert (await client.resolve_interrupt_request("per_1"))[0] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [[[True]], [[3]], [[None]], [[{}]], ["text"], "text", [["one", "two"]], [], [[]], [[""]]],
)
async def test_invalid_answers_never_reach_http(adapter, answers) -> None:
    client, helper, requests = adapter
    helper.normalize_event(form(required=True), session_id="ses_1")
    await bind(client)
    with pytest.raises(ValueError):
        await helper.question_reply("frm_1", answers=answers)
    assert requests == []
    assert (await client.resolve_interrupt_request("frm_1"))[0] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        {"type": "boolean"},
        {"type": "number"},
        {"type": "integer"},
        {"type": "external"},
        {"type": "unknown"},
        {"custom": "true"},
        {"options": [True]},
        {"when": []},
        {"minLength": 2},
    ],
)
async def test_unsupported_form_types_are_explicitly_rejected(adapter, field) -> None:
    _, helper, requests = adapter
    with pytest.raises(ValueError):
        helper.normalize_event(form(**field), session_id="ses_1")
    assert requests == []
    assert helper._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("multiple", [False, True])
async def test_options_custom_and_multiselect_match_native_types(adapter, multiple) -> None:
    client, helper, requests = adapter
    options = [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
    helper.normalize_event(
        form(type="multiselect" if multiple else "string", options=options), session_id="ses_1"
    )
    await bind(client)
    with pytest.raises(ValueError, match="declared question option"):
        await helper.question_reply("frm_1", answers=[["custom"]])
    assert requests == []
    chosen = ["a", "b"] if multiple else ["a"]
    await helper.question_reply("frm_1", answers=[chosen])
    assert json.loads(requests[-1].content) == {"answer": {"q0": chosen if multiple else "a"}}


@pytest.mark.asyncio
async def test_custom_text_is_not_coerced_or_trimmed(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(
        form(options=[{"value": "a", "label": "A"}], custom=True), session_id="ses_1"
    )
    await bind(client)
    await helper.question_reply("frm_1", answers=[["  custom  "]])
    assert json.loads(requests[-1].content) == {"answer": {"q0": "  custom  "}}
    assert requests[-1].method == "POST"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "unobserved", "expired", "session", "type"])
async def test_binding_required_before_get_reply_or_cancel(adapter, case) -> None:
    client, helper, requests = adapter
    if case != "unobserved":
        helper.normalize_event(form(), session_id="ses_1")
    if case != "missing":
        await bind(
            client,
            session="ses_other" if case == "session" else "ses_1",
            kind="permission" if case == "type" else "question",
            ttl_seconds=0.001 if case == "expired" else 100,
        )
    if case == "expired":
        client._interrupt_request_clock = lambda: float("inf")
    for operation in (
        helper.get("frm_1", interrupt_type="question"),
        helper.question_reply("frm_1", answers=[["x"]]),
        helper.question_reject("frm_1"),
    ):
        with pytest.raises(ValueError):
            await operation
    assert requests == []
    assert len(helper._pending) == (0 if case == "unobserved" else 1)


@pytest.mark.asyncio
async def test_question_cancel_uses_session_delete(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(form(), session_id="ses_1")
    await bind(client)
    assert await helper.question_reject("frm_1") is True
    assert requests[-1].method == "DELETE"
    assert requests[-1].url.path == "/api/session/ses_1/form/frm_1"
    assert requests[-1].content == b""


@pytest.mark.asyncio
async def test_concurrent_forms_keep_their_own_field_definitions(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(form("frm_a", "ses_a", required=True), session_id="ses_a")
    helper.normalize_event(
        form("frm_b", "ses_b", type="multiselect", options=[], custom=True), session_id="ses_b"
    )
    await bind(client, "frm_a", "ses_a")
    await bind(client, "frm_b", "ses_b")
    await asyncio.gather(
        helper.question_reply("frm_b", answers=[["b", "c"]]),
        helper.question_reply("frm_a", answers=[["a"]]),
    )
    payloads = {request.url.path: json.loads(request.content) for request in requests[1:]}
    assert payloads == {
        "/api/session/ses_a/form/frm_a/reply": {"answer": {"q0": "a"}},
        "/api/session/ses_b/form/frm_b/reply": {"answer": {"q0": ["b", "c"]}},
    }
    for session, request in (("ses_a", "frm_a"), ("ses_b", "frm_b")):
        resolved = helper.normalize_event(
            {"type": "form.replied", "properties": {"sessionID": session, "id": request}},
            session_id=session,
        )
        assert resolved is not None and resolved["type"] == "question.replied"
    assert helper._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "native,canonical",
    [("form.replied", "question.replied"), ("form.cancelled", "question.rejected")],
)
async def test_resolution_is_exactly_session_bound_and_evicts_cache(
    adapter, native, canonical
) -> None:
    _, helper, requests = adapter
    event = form()
    helper.normalize_event(event, session_id="ses_1")
    resolved = {"type": native, "properties": {"id": "frm_1", "sessionID": "ses_other"}}
    assert helper.normalize_event(resolved, session_id="ses_1") is None
    resolved["properties"]["sessionID"] = "ses_1"
    assert helper.normalize_event(resolved, session_id="ses_1")["type"] == canonical
    assert helper._pending == {}
    assert requests == []
    assert event == form()


@pytest.mark.asyncio
async def test_get_uses_observed_owned_route_and_validates_response(adapter) -> None:
    client, helper, requests = adapter
    original = form()
    helper.normalize_event(original, session_id="ses_1")
    await bind(client)
    detail = {**deepcopy(original["properties"]["form"]), "state": {"status": "pending"}}

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"version": "2.0.28"} if request.url.path == "/api/info" else {"data": detail}
        )

    client._client._transport = httpx.MockTransport(respond)
    assert await helper.get("frm_1", interrupt_type="question") == detail
    assert requests[-1].url.path == "/api/session/ses_1/form/frm_1"
    detail["sessionID"] = "ses_other"
    with pytest.raises(UpstreamContractError, match="mismatched ownership"):
        await helper.get("frm_1", interrupt_type="question")
    assert (await client.resolve_interrupt_request("frm_1"))[0] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 404, 409, 500])
async def test_failed_mutation_keeps_binding_until_callback_handles_error(adapter, status) -> None:
    client, helper, requests = adapter
    helper.normalize_event(form(), session_id="ses_1")
    await bind(client)

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/info":
            return httpx.Response(200, json={"version": "2.0.28"})
        return httpx.Response(status, json={})

    client._client._transport = httpx.MockTransport(respond)
    with pytest.raises(UpstreamContractError if status == 200 else httpx.HTTPStatusError):
        await helper.question_reply("frm_1", answers=[["a"]])
    assert (await client.resolve_interrupt_request("frm_1"))[0] == "active"
    assert ("ses_1", "frm_1") in helper._pending


@pytest.mark.asyncio
async def test_permission_resolution_normalizes_request_id_and_removes_observation(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(permission(), session_id="ses_1")
    await bind(client, "per_1", kind="permission")
    event = {
        "type": "permission.replied",
        "properties": {
            "sessionID": "ses_1",
            "requestID": "per_1",
            "reply": "reject",
        },
    }
    assert helper.normalize_event(event, session_id="ses_1") == event
    with pytest.raises(ValueError, match="observed request"):
        await helper.permission_reply("per_1", reply="once")
    assert requests == []


@pytest.mark.asyncio
async def test_other_sessions_are_ignored_and_generic_forms_explicitly_reject(adapter) -> None:
    _, helper, requests = adapter
    assert helper.normalize_event(form(), session_id="ses_other") is None
    generic = form()
    generic["properties"]["form"]["metadata"] = {"kind": "oauth"}
    with pytest.raises(ValueError, match="Unsupported V2 form kind"):
        helper.normalize_event(generic, session_id="ses_1")
    assert helper._pending == {}
    assert requests == []


@pytest.mark.asyncio
async def test_event_field_cache_does_not_alias_source_or_normalized_payload(adapter) -> None:
    client, helper, requests = adapter
    event = form(options=[{"value": "a", "label": "A"}])
    normalized = helper.normalize_event(event, session_id="ses_1")
    event["properties"]["form"]["fields"][0]["options"][0]["value"] = "source-mutated"
    normalized["properties"]["questions"][0]["options"][0]["value"] = "output-mutated"
    await bind(client)
    await helper.question_reply("frm_1", answers=[["a"]])
    assert json.loads(requests[-1].content) == {"answer": {"q0": "a"}}
    resolved = helper.normalize_event(
        {"type": "form.replied", "properties": {"sessionID": "ses_1", "id": "frm_1"}},
        session_id="ses_1",
    )
    assert resolved is not None and resolved["type"] == "question.replied"
    assert helper._pending == {}


@pytest.mark.asyncio
async def test_network_error_preserves_pending_request_for_retry(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(form(), session_id="ses_1")
    await bind(client)

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/info":
            return httpx.Response(200, json={"version": "2.0.28"})
        raise httpx.ConnectError("simulated upstream loss", request=request)

    client._client._transport = httpx.MockTransport(respond)
    with pytest.raises(httpx.ConnectError):
        await helper.question_reject("frm_1")
    assert (await client.resolve_interrupt_request("frm_1"))[0] == "active"
    assert ("ses_1", "frm_1") in helper._pending


@pytest.mark.asyncio
async def test_lost_execution_lease_rejects_callback_before_http(adapter) -> None:
    client, _, requests = adapter

    def invalid_lease(session: str) -> None:
        assert session == "ses_1"
        raise ValueError("Observation is poisoned")

    helper = V2Interventions(client, validate_lease=invalid_lease)
    helper.normalize_event(form(), session_id="ses_1")
    await bind(client)
    with pytest.raises(ValueError, match="poisoned"):
        await helper.question_reply("frm_1", answers=[["Yes"]])
    assert requests == []


@pytest.mark.asyncio
async def test_simultaneous_callbacks_make_only_one_native_mutation(adapter) -> None:
    client, helper, requests = adapter
    helper.normalize_event(form(custom=True), session_id="ses_1")
    await bind(client)
    results = await asyncio.gather(
        helper.question_reply("frm_1", answers=[["Yes"]]),
        helper.question_reply("frm_1", answers=[["Yes"]]),
        return_exceptions=True,
    )
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert sum(request.method == "POST" for request in requests) == 1

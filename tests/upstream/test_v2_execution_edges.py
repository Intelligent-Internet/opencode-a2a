from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx
import pytest

from opencode_a2a.upstream.contracts import ExecutionState, ExecutionUpdate
from opencode_a2a.upstream.v2_events import V2ExecutionUncertainError
from tests.upstream.test_v2_execution import NativeServer


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["delete-status", "interrupt-http", "interrupt-shape"])
async def test_abort_failure_is_explicit_uncertainty_and_poisons_the_invocation(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    server = NativeServer(monkeypatch, "waiting")

    async def broken_abort(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE" and failure == "delete-status":
            return httpx.Response(200)
        if request.url.path.endswith("/interrupt"):
            return (
                httpx.Response(503)
                if failure == "interrupt-http"
                else httpx.Response(200, json={"interrupted": "yes"})
            )
        return await server.handle(request)

    monkeypatch.setattr(server.client._client, "_transport", httpx.MockTransport(broken_abort))
    task = asyncio.create_task(server.engine.submit("ses_one", "test"))
    try:
        await server.admitted.wait()
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.abort("ses_one")
        results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert len(results) == 1 and isinstance(results[0], V2ExecutionUncertainError)
        assert server.engine.sessions["ses_one"].subscription.error is not None
        assert len(server.inputs) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_native_ephemeral_delta_and_snapshot_keep_one_part_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "delta")
    try:
        result = await server.engine.submit("ses_one", "test", emit_updates=True)
        delta = await server.engine.next_update("ses_one")
        snapshot = await server.engine.next_update("ses_one")
        assert isinstance(delta, dict) and delta["type"] == "message.part.delta"
        assert isinstance(snapshot, dict) and snapshot["type"] == "message.part.updated"
        properties = delta["properties"]
        updated = snapshot["properties"]
        assert isinstance(properties, Mapping) and isinstance(updated, Mapping)
        part = updated["part"]
        assert isinstance(part, Mapping) and properties["partID"] == part["id"]
        assert properties["delta"] == "ses_one" and properties["field"] == "text"
        assert result.outcome is not None and result.outcome.output == "ses_one"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_stream_emits_typed_failed_terminal_only_after_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "failed")
    try:
        result = await server.engine.submit("ses_one", "test", emit_updates=True)
        await server.engine.next_update("ses_one")
        terminal = await server.engine.next_update("ses_one")
        assert isinstance(terminal, ExecutionUpdate)
        assert terminal.terminal == result.outcome
        assert terminal.terminal is not None and terminal.terminal.state == ExecutionState.FAILED
        assert server.engine.sessions["ses_one"].updates.empty()
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["session.synthetic", "session.shell.started"])
async def test_unexpected_idle_work_prevents_admission(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    server = NativeServer(monkeypatch)
    try:
        await server.hub.start()
        server.feed.send(server.event("ses_one", kind))
        subscription = server.engine.sessions["ses_one"].subscription
        # Observe the actual HTTP event feed before attempting admission.
        for _ in range(20):
            if not subscription.queue.empty():
                break
            await asyncio.sleep(0)
        assert subscription.queue.qsize() == 1
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_one", "test")
        assert server.inputs == []
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unary_has_no_updates_and_new_stream_drops_previous_invocation_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    try:
        for _ in range(3):
            await server.engine.submit("ses_one", "unary")
        state = server.engine.sessions["ses_one"]
        assert state.updates.empty()
        await server.engine.submit("ses_one", "old stream", emit_updates=True)
        assert state.updates.qsize() == 2
        await server.engine.submit("ses_one", "new stream", emit_updates=True)
        assert state.updates.qsize() == 2
        update = await server.engine.next_update("ses_one")
        assert isinstance(update, dict)
        properties = update["properties"]
        assert isinstance(properties, Mapping)
        part = properties["part"]
        assert isinstance(part, Mapping) and part["messageID"] == "msg_assistant_5_ses_one"
        assert len(server.inputs) == 5 and len(set(server.inputs)) == 5
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_same_session_lease_serializes_invocations(monkeypatch: pytest.MonkeyPatch) -> None:
    server = NativeServer(monkeypatch, "waiting")
    first = asyncio.create_task(server.engine.submit("ses_one", "first"))
    second: asyncio.Task[object] | None = None
    try:
        await server.admitted.wait()
        second = asyncio.create_task(server.engine.submit("ses_one", "second"))
        await asyncio.sleep(0)
        assert len(server.inputs) == 1
        server.mode = "success"
        server.execution("ses_one", server.inputs[0])
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result.receipt.message_id != second_result.receipt.message_id
        assert len(server.inputs) == 2
    finally:
        first.cancel()
        if second is not None:
            second.cancel()
            await asyncio.gather(second, return_exceptions=True)
        await asyncio.gather(first, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_disconnect_wakes_invocation_and_update_listener_without_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    submission = asyncio.create_task(server.engine.submit("ses_one", "test", emit_updates=True))
    listener = asyncio.create_task(server.engine.next_update("ses_one"))
    try:
        await server.admitted.wait()
        server.feed.queue.put_nowait(None)
        results = await asyncio.wait_for(
            asyncio.gather(submission, listener, return_exceptions=True), 1
        )
        assert len(results) == 2 and all(
            isinstance(result, V2ExecutionUncertainError) for result in results
        )
        assert sum(request.url.path == "/api/event" for request in server.requests) == 1
        assert len(server.inputs) == 1
    finally:
        submission.cancel()
        listener.cancel()
        await asyncio.gather(submission, listener, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_invocation_owner_cancellation_quarantines_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    task = asyncio.create_task(server.engine.submit("ses_one", "test"))
    try:
        await server.admitted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_one", "retry")
        assert len(server.requests) == count
        assert len(server.inputs) == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_native_form_nested_session_and_resolution_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    task = asyncio.create_task(server.engine.submit("ses_one", "test", emit_updates=True))
    try:
        await server.admitted.wait()
        server.feed.send(
            {
                "id": "evt_form",
                "type": "form.created",
                "data": {"form": {"id": "form_one", "sessionID": "ses_one", "fields": []}},
            }
        )
        created = await asyncio.wait_for(server.engine.next_update("ses_one"), 1)
        assert isinstance(created, dict) and created["type"] == "form.created"
        assert created["properties"] == {
            "form": {"id": "form_one", "sessionID": "ses_one", "fields": []}
        }
        server.feed.send(
            {
                "id": "evt_reply",
                "type": "form.replied",
                "data": {"id": "form_one", "sessionID": "ses_one", "answer": {"q0": "Yes"}},
            }
        )
        resolved = await asyncio.wait_for(server.engine.next_update("ses_one"), 1)
        assert isinstance(resolved, dict) and resolved["type"] == "form.replied"
        assert resolved["properties"] == {
            "id": "form_one",
            "sessionID": "ses_one",
            "answer": {"q0": "Yes"},
        }
        server.execution("ses_one", server.inputs[0])
        result = await task
        assert result.outcome is not None and result.outcome.output == "ses_one"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_projection_pagination_is_bounded_and_cursor_does_not_combine_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    pages = 0

    async def paginated(request: httpx.Request) -> httpx.Response:
        nonlocal pages
        if request.url.path.endswith("/message"):
            pages += 1
            if pages == 1:
                assert request.url.params["order"] == "desc"
                return httpx.Response(
                    200,
                    json={"data": [server.messages["ses_one"][0]], "cursor": {"next": "opaque"}},
                )
            assert request.url.params["cursor"] == "opaque" and "order" not in request.url.params
            return httpx.Response(200, json={"data": server.messages["ses_one"][1:], "cursor": {}})
        return await server.handle(request)

    monkeypatch.setattr(server.client._client, "_transport", httpx.MockTransport(paginated))
    try:
        result = await server.engine.submit("ses_one", "test")
        assert result.outcome is not None and result.outcome.output == "ses_one"
        assert pages == 2
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_no_terminal_update_escapes_when_exact_projection_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "missing-projection")
    try:
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_one", "test", emit_updates=True)
        queue = server.engine.sessions["ses_one"].updates
        update = queue.get_nowait()
        assert isinstance(update, dict) and update["type"] == "message.part.updated"
        assert queue.empty()
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["stream", "request"])
async def test_existing_upstream_concurrency_budgets_are_not_bypassed(
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    server = NativeServer(monkeypatch)
    budget = server.client._stream_budget if category == "stream" else server.client._request_budget
    monkeypatch.setattr(budget, "_limit", 1)
    try:
        async with budget.reserve(operation="occupied"):
            with pytest.raises(V2ExecutionUncertainError):
                await server.engine.submit("ses_one", "test")
        assert server.inputs == []
        assert server.engine.sessions["ses_one"].subscription.error is not None
    finally:
        await server.close()

from __future__ import annotations

import asyncio

import httpx
import pytest

from opencode_a2a.execution.stream_events import normalize_v1_event
from opencode_a2a.upstream.v2_events import V2ExecutionUncertainError
from tests.upstream.test_v2_execution import NativeServer


@pytest.mark.asyncio
@pytest.mark.parametrize("prerequisite", ["interrupt", "delete"])
async def test_abort_barrier_outlives_natural_completion(
    monkeypatch: pytest.MonkeyPatch, prerequisite: str
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    entered = asyncio.Event()
    release = asyncio.Event()
    interrupts: list[str] = []

    async def delayed(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/interrupt"):
            interrupts.append(request.url.path)
            if prerequisite == "interrupt":
                entered.set()
                await release.wait()
            return httpx.Response(200, json={"interrupted": False})
        if request.method == "DELETE" and prerequisite == "delete":
            entered.set()
            await release.wait()
            return httpx.Response(204)
        return await server.handle(request)

    monkeypatch.setattr(server.client._client, "_transport", httpx.MockTransport(delayed))
    first = asyncio.create_task(server.engine.submit("ses_one", "A"))
    abort: asyncio.Task[object] | None = None
    second: asyncio.Task[object] | None = None
    try:
        await server.admitted.wait()
        abort = asyncio.create_task(server.engine.abort("ses_one", server.inputs[0]))
        await asyncio.wait_for(entered.wait(), 1)
        server.execution("ses_one", server.inputs[0])
        result = await asyncio.wait_for(first, 1)
        server.mode = "success"
        second = asyncio.create_task(server.engine.submit("ses_one", "B"))
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(server.inputs) == 1
        assert not second.done() and not abort.done()
        release.set()
        assert await asyncio.wait_for(abort, 1) == result
        await asyncio.wait_for(second, 1)
        assert len(server.inputs) == 2
        assert len(interrupts) == (1 if prerequisite == "interrupt" else 0)
    finally:
        release.set()
        tasks = [task for task in (first, abort, second) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_idle_lru_retirement_allows_fresh_sessions_and_forbids_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    server.hub.session_limit = 2
    try:
        await server.engine.submit("ses_one", "one")
        server.engine.ensure_capacity()
        server.engine.register_created("ses_two", "/workspace")
        await server.engine.submit("ses_two", "two")
        # Touch one: two must be the LRU victim despite its later creation.
        await server.engine.submit("ses_one", "again")
        server.engine.ensure_capacity()
        server.engine.register_created("ses_three", "/workspace")
        await server.engine.submit("ses_three", "three")
        assert set(server.engine.sessions) == {"ses_one", "ses_three"}
        assert set(server.hub.sessions) == {"ses_one", "ses_three"}
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError, match="gateway-created"):
            await server.engine.submit("ses_two", "retired")
        assert len(server.requests) == count
        for index in range(5):
            server.engine.ensure_capacity()
            session = f"ses_fresh{index}"
            server.engine.register_created(session, "/workspace")
            await server.engine.submit(session, "fresh")
            assert len(server.engine.sessions) == len(server.hub.sessions) == 2
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_active_and_quarantined_capacity_fail_before_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    server.hub.session_limit = 1
    task = asyncio.create_task(server.engine.submit("ses_one", "active"))
    try:
        await server.admitted.wait()
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError, match="capacity"):
            server.engine.ensure_capacity()
        assert len(server.requests) == count
        assert set(server.engine.sessions) == {"ses_one"}
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        with pytest.raises(V2ExecutionUncertainError, match="capacity"):
            server.engine.ensure_capacity()
        assert len(server.requests) == count
        assert server.hub.sessions["ses_one"].error is not None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_active_execution_budget_spans_settlement_not_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    server.engine.max_active = 1
    server.engine.register_created("ses_two", "/workspace")
    first = asyncio.create_task(server.engine.submit("ses_one", "A"))
    try:
        await server.admitted.wait()
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError, match="Active execution capacity"):
            await server.engine.submit("ses_two", "B")
        assert len(server.requests) == count and len(server.inputs) == 1
        assert server.engine.sessions["ses_two"].subscription.error is None
        server.execution("ses_one", server.inputs[0])
        await first
        server.mode = "success"
        result = await server.engine.submit("ses_two", "B")
        assert result.outcome is not None and result.outcome.output == "ses_two"
        assert len(server.inputs) == 2 and server.engine._active == 0
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_uncertain_native_execution_does_not_release_a_quarantined_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    server.engine.max_active = 1
    server.engine.register_created("ses_two", "/workspace")
    task = asyncio.create_task(server.engine.submit("ses_one", "A"))
    try:
        await server.admitted.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError, match="Active execution capacity"):
            await server.engine.submit("ses_two", "B")
        assert len(server.requests) == count and len(server.inputs) == 1
        assert server.engine._active == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
async def test_intervention_guard_and_expected_abort_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    task: asyncio.Task[object] | None = None
    try:
        with pytest.raises(V2ExecutionUncertainError, match="in-flight"):
            server.engine.validate_intervention("ses_one")
        assert server.requests == []
        task = asyncio.create_task(server.engine.submit("ses_one", "A"))
        await server.admitted.wait()
        assert server.engine.validate_intervention("ses_one") == server.inputs[0]
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.abort("ses_one", "msg_earlier_task")
        assert len(server.requests) == count
        server.engine.sessions["ses_one"].subscription.poison("test quarantine")
        with pytest.raises(V2ExecutionUncertainError, match="test quarantine"):
            server.engine.validate_intervention("ses_one")
        assert len(server.requests) == count
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_reasoning_and_tool_results_are_structured_before_model_continuation(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    task = asyncio.create_task(server.engine.submit("ses_one", "read", emit_updates=True))
    try:
        await server.admitted.wait()
        session = "ses_one"
        assistant = "msg_step"
        events: list[tuple[str, dict[str, object]]] = [
            ("session.inbox.enqueued", {"inboxID": server.inputs[0], "item": {"type": "user"}}),
            ("session.inbox.delivered", {"inboxID": server.inputs[0]}),
            ("session.step.started", {"assistantMessageID": assistant}),
            ("session.reasoning.started", {"assistantMessageID": assistant, "ordinal": 0}),
            (
                "session.reasoning.delta",
                {"assistantMessageID": assistant, "ordinal": 0, "delta": "inspect"},
            ),
            (
                "session.reasoning.ended",
                {"assistantMessageID": assistant, "ordinal": 0, "text": "inspect"},
            ),
            (
                "session.tool.input.started",
                {"assistantMessageID": assistant, "id": "call_read", "name": "read"},
            ),
            (
                "session.tool.called",
                {
                    "assistantMessageID": assistant,
                    "id": "call_read",
                    "input": {"path": "example.txt"},
                    "executed": True,
                },
            ),
            (
                "session.tool.progress",
                {
                    "assistantMessageID": assistant,
                    "id": "call_read",
                    "metadata": {"title": "Reading"},
                },
            ),
            (
                "session.tool.failed" if failed else "session.tool.success",
                {
                    "assistantMessageID": assistant,
                    "id": "call_read",
                    "content": [
                        {"type": "text", "text": "file contents"},
                        {
                            "type": "file",
                            "uri": "file:///workspace/example.txt",
                            "mime": "text/plain",
                        },
                    ],
                    "executed": True,
                    **({"error": {"type": "tool", "message": "read failure"}} if failed else {}),
                },
            ),
        ]
        for kind, data in events:
            server.feed.send(server.event(session, kind, **data))
        updates = []
        for _ in range(7):
            raw = await asyncio.wait_for(server.engine.next_update(session), 1)
            assert isinstance(raw, dict)
            update = normalize_v1_event(raw)
            assert update is not None
            updates.append(update)
        assert [update.part_type for update in updates[:3]] == ["reasoning", None, "reasoning"]
        assert updates[1].delta == "inspect" and updates[2].snapshot == "inspect"
        terminal = updates[-1].tool_payload
        assert terminal is not None and terminal["tool"] == "read"
        assert terminal["call_id"] == "call_read"
        assert terminal["status"] == ("error" if failed else "completed")
        assert terminal["output"] == [
            {"type": "text", "text": "file contents"},
            {"type": "file", "uri": "file:///workspace/example.txt", "mime": "text/plain"},
        ]
        assert not task.done()  # No model continuation or execution terminal was required.
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()

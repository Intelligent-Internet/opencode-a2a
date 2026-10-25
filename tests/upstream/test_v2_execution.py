from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from opencode_a2a.upstream.contracts import ExecutionState
from opencode_a2a.upstream.v2 import V2UpstreamAdapter
from opencode_a2a.upstream.v2_events import V2EventHub, V2ExecutionUncertainError
from opencode_a2a.upstream.v2_execution import V2ExecutionEngine
from tests.support.settings import make_settings


class EventStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.closed = False

    def send(self, payload: dict[str, object]) -> None:
        self.queue.put_nowait(b"data: " + json.dumps(payload).encode() + b"\n\n")

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self.queue.get()
            if chunk is None:
                return
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class NativeServer:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, mode: str = "success") -> None:
        self.mode = mode
        self.feed = EventStream()
        self.feed.send({"type": "server.connected"})
        self.requests: list[httpx.Request] = []
        self.messages: dict[str, list[dict[str, object]]] = {}
        self.inputs: list[str] = []
        self.sequences: dict[str, int] = {}
        self.admitted = asyncio.Event()
        self.client = V2UpstreamAdapter(make_settings())
        monkeypatch.setattr(self.client._client, "_transport", httpx.MockTransport(self.handle))
        self.hub = V2EventHub(self.client, ready_timeout=0.1)
        self.engine = V2ExecutionEngine(self.client, self.hub, timeout=0.5)
        self.engine.register_created("ses_one", "/workspace")

    def event(self, session: str, kind: str, **data: object) -> dict[str, object]:
        seq = self.sequences.get(session, 0) + 3
        self.sequences[session] = seq
        return {
            "id": f"evt_{session}_{seq}",
            "type": kind,
            "data": {"sessionID": session, **data},
            "durable": {"aggregateID": session, "seq": str(seq)},
        }

    def execution(self, session: str, identity: str, *, interrupted: bool = False) -> None:
        assistant = f"msg_assistant_{len(self.inputs)}_{session}"
        events = [
            self.event(session, "session.inbox.enqueued", inboxID=identity, item={"type": "user"}),
            self.event(session, "session.inbox.delivered", inboxID=identity),
            self.event(session, "session.step.started", assistantMessageID=assistant),
            self.event(
                session, "session.text.ended", assistantMessageID=assistant, ordinal=0, text=session
            ),
        ]
        terminal = (
            "interrupted" if interrupted else "failed" if self.mode == "failed" else "succeeded"
        )
        end = self.event(
            session, f"session.execution.{terminal}", reason="user", error={"type": "provider"}
        )
        events.append(end)
        if self.mode == "missing-delivery":
            events.pop(1)
        if self.mode == "duplicate":
            events.insert(1, events[0])
        if self.mode == "reorder":
            events[1], events[2] = events[2], events[1]
        if self.mode == "foreign-input":
            events.insert(
                1,
                self.event(
                    session, "session.inbox.enqueued", inboxID="msg_foreign", item={"type": "user"}
                ),
            )
        if self.mode == "synthetic":
            events[0] = self.event(
                session, "session.inbox.enqueued", inboxID=identity, item={"type": "synthetic"}
            )
        if self.mode == "missing-terminal":
            events.pop()
        if self.mode == "unsequenced-terminal":
            end.pop("durable")
        if self.mode == "foreign-step":
            events[3] = self.event(
                session,
                "session.text.ended",
                assistantMessageID="msg_foreign",
                ordinal=0,
                text="bad",
            )
        if self.mode == "delta":
            events.insert(
                3,
                {
                    "id": f"evt_delta_{session}",
                    "type": "session.text.delta",
                    "data": {
                        "sessionID": session,
                        "assistantMessageID": assistant,
                        "ordinal": 0,
                        "delta": session,
                    },
                },
            )
        baseline = self.sequences[session]
        for index, event in enumerate(events, start=1):
            if "durable" in event:
                seq = baseline + index * 3
                event["id"] = f"evt_{session}_{seq}"
                event["durable"] = {"aggregateID": session, "seq": str(seq)}
                self.sequences[session] = seq
        self.messages[session] = [
            {"id": str(end["id"]).replace("evt_", "msg_", 1), "type": "idle", "outcome": terminal},
            {"id": assistant, "type": "assistant", "content": [{"type": "text", "text": session}]},
            {"id": identity, "type": "user"},
            {
                "id": "msg_unrelated",
                "type": "assistant",
                "content": [{"type": "text", "text": "foreign"}],
            },
        ]
        if self.mode == "missing-projection":
            self.messages[session].pop(1)
        if self.mode == "wrong-idle":
            self.messages[session][0]["outcome"] = "failed"
        for event in events:
            self.feed.send(event)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        session = path.split("/")[3] if path.startswith("/api/session/ses_") else "ses_one"
        if path == "/api/event":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=self.feed
            )
        if path == "/api/session/active":
            return httpx.Response(
                200,
                json={"data": {"ses_one": {"type": "running"}} if self.mode == "active" else {}},
            )
        if path.endswith("/inbox"):
            return httpx.Response(
                200, json={"data": [{"id": "foreign"}] if self.mode == "inbox" else []}
            )
        if path.endswith("/wait") or request.method == "DELETE":
            return httpx.Response(204)
        if path.endswith("/message"):
            return httpx.Response(200, json={"data": self.messages[session], "cursor": {}})
        if path.endswith("/interrupt"):
            self.execution(session, self.inputs[-1], interrupted=True)
            return httpx.Response(200, json={"interrupted": True})
        if path.endswith("/prompt"):
            body = json.loads(request.content)
            identity = body["id"]
            self.inputs.append(identity)
            self.admitted.set()
            if self.mode == "disconnect":
                self.feed.queue.put_nowait(None)
            elif self.mode != "waiting":
                self.execution(session, identity)
            if self.mode == "http-error":
                return httpx.Response(503)
            return httpx.Response(
                200, json={"data": {"id": "msg_wrong" if self.mode == "bad-receipt" else identity}}
            )
        raise AssertionError(f"Unexpected HTTP boundary: {request.method} {path}")

    async def close(self) -> None:
        await self.hub.close()
        await self.client.close()


@pytest.mark.asyncio
async def test_native_success_exact_projection_and_followup_unique_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    try:
        first = await server.engine.submit("ses_one", "first", emit_updates=True)
        second = await server.engine.submit("ses_one", "followup", emit_updates=True)
        assert first.outcome is not None and first.outcome.state == ExecutionState.COMPLETED
        assert first.outcome.output == "ses_one" and second.outcome == first.outcome.__class__(
            first.outcome.session,
            ExecutionState.COMPLETED,
            "ses_one",
            message_id="msg_assistant_2_ses_one",
        )
        assert first.receipt.message_id != second.receipt.message_id
        assert len(set(server.inputs)) == 2 and all(
            identity.startswith("msg_") for identity in server.inputs
        )
        assert sum(request.url.path == "/api/event" for request in server.requests) == 1
        prompts = [
            json.loads(request.content)
            for request in server.requests
            if request.url.path.endswith("/prompt")
        ]
        assert prompts[0] == {
            "id": first.receipt.message_id,
            "text": "first",
            "delivery": "queue",
            "resume": True,
        }
        assert any(
            request.url.path == "/api/experimental/session/ses_one/wait" and not request.content
            for request in server.requests
        )
        update = await server.engine.next_update("ses_one")
        assert isinstance(update, dict)
        assert update["type"] == "message.part.updated" and "properties" in update
        terminal = await server.engine.next_update("ses_one")
        assert terminal == {"type": "session.idle", "properties": {"sessionID": "ses_one"}}
    finally:
        await server.close()
    assert server.feed.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "missing-delivery",
        "duplicate",
        "reorder",
        "foreign-input",
        "synthetic",
        "missing-terminal",
        "unsequenced-terminal",
        "foreign-step",
        "missing-projection",
        "wrong-idle",
        "http-error",
        "bad-receipt",
        "disconnect",
        "active",
        "inbox",
    ],
)
async def test_uncertainty_quarantines_without_blind_resend(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    server = NativeServer(monkeypatch, mode)
    try:
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_one", "test")
        count = len(server.requests)
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_one", "retry forbidden")
        assert len(server.requests) == count
        assert len(server.inputs) == (0 if mode in {"active", "inbox"} else 1)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_native_terminal_is_an_attributed_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "failed")
    try:
        result = await server.engine.submit("ses_one", "test")
        assert result.outcome is not None and result.outcome.state == ExecutionState.FAILED
        assert (
            result.outcome.output == "ses_one"
            and result.outcome.error_type == "upstream_execution_failed"
        )
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_parallel_sessions_share_feed_and_keep_output_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    server.engine.register_created("ses_two", "/workspace")
    try:
        first, second = await asyncio.gather(
            server.engine.submit("ses_one", "one"), server.engine.submit("ses_two", "two")
        )
        assert first.outcome is not None and first.outcome.output == "ses_one"
        assert second.outcome is not None and second.outcome.output == "ses_two"
        assert len(server.inputs) == 2 and len(set(server.inputs)) == 2
        assert sum(request.url.path == "/api/event" for request in server.requests) == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unknown_session_and_abort_without_lease_have_no_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch)
    try:
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_foreign", "test")
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.abort("ses_one")
        assert server.requests == []
        assert server.inputs == []
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_abort_returns_only_observed_interrupted_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    task = asyncio.create_task(server.engine.submit("ses_one", "wait"))
    try:
        await server.admitted.wait()
        result = await server.engine.abort("ses_one")
        assert await task == result
        assert result.outcome is not None and result.outcome.state == ExecutionState.CANCELLED
        interrupt = next(
            request for request in server.requests if request.url.path.endswith("/interrupt")
        )
        assert interrupt.url.params["resume"] == "false" and not interrupt.content
        assert sum(request.method == "DELETE" for request in server.requests) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["events", "updates", "frame", "barrier"])
async def test_bounded_queues_frames_and_readiness_fail_closed(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    server = NativeServer(monkeypatch)
    if limit == "events":
        server.engine.sessions["ses_one"].subscription.queue = asyncio.Queue(1)
    if limit == "updates":
        server.engine.sessions["ses_one"].updates = asyncio.Queue(1)
    if limit == "frame":
        server.hub.frame_limit = 10
    if limit == "barrier":
        server.feed.queue.get_nowait()
    try:
        with pytest.raises(V2ExecutionUncertainError):
            await server.engine.submit("ses_one", "test", emit_updates=True)
        assert server.engine.sessions["ses_one"].subscription.error is not None
        assert len(server.inputs) == (0 if limit in {"barrier", "frame"} else 1)
        if limit == "barrier":
            assert server.hub._task is not None and server.hub._task.done()
            assert server.feed.closed
            assert server.client._stream_budget._inflight == 0
    finally:
        await server.close()

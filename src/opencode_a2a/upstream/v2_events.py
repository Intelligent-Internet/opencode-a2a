"""One fail-closed native event connection, shared by gateway-owned sessions.

The owner must close this hub before closing the existing upstream HTTP client.
There is deliberately no replay or reconnect: losing observation invalidates ownership.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..opencode_upstream_client import OpencodeUpstreamClient, UpstreamContractError


class V2ExecutionUncertainError(UpstreamContractError):
    """The invocation cannot be safely attributed; its session must not be reused."""


@dataclass(frozen=True)
class NativeEvent:
    id: str
    type: str
    data: Mapping[str, object]
    aggregate: str | None = None
    seq: int | None = None


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise V2ExecutionUncertainError("Native event object is malformed")
    return dict(value)


@dataclass
class SessionSubscription:
    queue: asyncio.Queue[NativeEvent]
    error: V2ExecutionUncertainError | None = None
    failed: asyncio.Event = field(default_factory=asyncio.Event)
    seq: int | None = None

    def check(self) -> None:
        if self.error is not None:
            raise self.error

    def poison(self, reason: str) -> None:
        if self.error is None:
            self.error = V2ExecutionUncertainError(reason)
            self.failed.set()

    async def receive(self) -> NativeEvent:
        self.check()
        pending = asyncio.create_task(self.queue.get())
        failed = asyncio.create_task(self.failed.wait())
        try:
            await asyncio.wait((pending, failed), return_when=asyncio.FIRST_COMPLETED)
            self.check()
            return pending.result()
        finally:
            pending.cancel()
            failed.cancel()
            await asyncio.gather(pending, failed, return_exceptions=True)


class V2EventHub:
    """Bounded single-reader fanout using the adapter's HTTP client and stream budget."""

    def __init__(
        self,
        client: OpencodeUpstreamClient,
        *,
        queue_limit: int = 256,
        session_limit: int = 1024,
        frame_limit: int = 262144,
        ready_timeout: float = 10,
    ) -> None:
        if min(queue_limit, session_limit, frame_limit) < 1 or ready_timeout <= 0:
            raise ValueError("Event hub limits must be positive")
        self.client = client
        self.queue_limit = queue_limit
        self.session_limit = session_limit
        self.frame_limit = frame_limit
        self.ready_timeout = ready_timeout
        self.sessions: dict[str, SessionSubscription] = {}
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._error: V2ExecutionUncertainError | None = None
        self._closed = False

    def register(self, session: str) -> SessionSubscription:
        self.check()
        if not session or session in self.sessions:
            raise ValueError("Session must be new and nonempty")
        if len(self.sessions) >= self.session_limit:
            raise V2ExecutionUncertainError("Native event session limit exceeded")
        subscription = SessionSubscription(asyncio.Queue(self.queue_limit))
        self.sessions[session] = subscription
        return subscription

    def check(self) -> None:
        if self._error is not None:
            raise self._error
        if self._closed:
            raise V2ExecutionUncertainError("Native event hub is closed")

    def unregister(self, session: str) -> None:
        """Retire observation permanently for an engine-evicted idle session."""
        subscription = self.sessions.pop(session)
        subscription.poison("Gateway session ownership was retired")

    async def start(self) -> None:
        self.check()
        if self._task is None:
            self._task = asyncio.create_task(self._read())
        try:
            await asyncio.wait_for(self._ready.wait(), self.ready_timeout)
        except TimeoutError as exc:
            self._fail("Native event server.connected barrier timed out")
            if self._task is not None:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            raise V2ExecutionUncertainError("Native event readiness is uncertain") from exc
        self.check()

    def _fail(self, reason: str) -> None:
        if self._error is None:
            self._error = V2ExecutionUncertainError(reason)
        for subscription in self.sessions.values():
            subscription.poison(reason)
        self._ready.set()

    def _dispatch(self, payload: object) -> None:
        envelope = _object(payload)
        kind = envelope.get("type")
        if kind == "server.connected":
            if self._ready.is_set():
                raise V2ExecutionUncertainError("Repeated native event readiness barrier")
            self._ready.set()
            return
        if not self._ready.is_set():
            raise V2ExecutionUncertainError("Native event arrived before server.connected")
        identity = envelope.get("id")
        if not isinstance(identity, str) or not isinstance(kind, str):
            raise V2ExecutionUncertainError("Native event identity is missing")
        data = _object(envelope.get("data"))
        form = data.get("form")
        nested = _object(form) if isinstance(form, dict) else {}
        session = data.get("sessionID", nested.get("sessionID"))
        durable = envelope.get("durable")
        aggregate: str | None = None
        seq: int | None = None
        if durable is not None:
            record = _object(durable)
            rawseq = record.get("seq")
            rawaggregate = record.get("aggregateID")
            aggregate = rawaggregate if isinstance(rawaggregate, str) else None
            if isinstance(rawseq, bool) or not (
                isinstance(rawseq, int) or isinstance(rawseq, str) and rawseq.isdecimal()
            ):
                raise V2ExecutionUncertainError("Native durable sequence is invalid")
            seq = int(rawseq)
            if aggregate is None or seq < 0:
                raise V2ExecutionUncertainError("Native durable aggregate is invalid")
        if not isinstance(session, str):
            session = aggregate
        subscription = self.sessions.get(session) if isinstance(session, str) else None
        if subscription is None or subscription.error is not None:
            return
        if seq is not None:
            if aggregate != session or subscription.seq is not None and seq <= subscription.seq:
                subscription.poison("Native durable sequence regressed or duplicated")
                return
            # Sequences may skip records not published on this native feed.
            subscription.seq = seq
        try:
            subscription.queue.put_nowait(NativeEvent(identity, kind, data, aggregate, seq))
        except asyncio.QueueFull:
            subscription.poison("Native session event queue overflowed")

    async def _read(self) -> None:
        try:
            async with self.client._stream_budget.reserve(operation="/api/event"):
                async with self.client._client.stream(
                    "GET",
                    "/api/event",
                    timeout=self.client.stream_timeout,
                    headers=self.client._request_headers({"Accept": "text/event-stream"}),
                ) as response:
                    response.raise_for_status()
                    if (
                        response.headers.get("content-type", "").split(";", 1)[0]
                        != "text/event-stream"
                    ):
                        raise V2ExecutionUncertainError("Native event response is not SSE")
                    buffer = b""
                    async for chunk in response.aiter_bytes():
                        # Incremental splitting bounds the parser even for a large HTTP chunk.
                        for offset in range(0, len(chunk), 4096):
                            buffer += chunk[offset : offset + 4096]
                            buffer = buffer.replace(b"\r\n", b"\n")
                            while b"\n\n" in buffer:
                                frame, buffer = buffer.split(b"\n\n", 1)
                                if len(frame) > self.frame_limit:
                                    raise V2ExecutionUncertainError(
                                        "Native event frame limit exceeded"
                                    )
                                lines = [
                                    line[5:].lstrip()
                                    for line in frame.split(b"\n")
                                    if line.startswith(b"data:")
                                ]
                                if lines:
                                    self._dispatch(json.loads(b"\n".join(lines)))
                            if len(buffer) > self.frame_limit:
                                raise V2ExecutionUncertainError("Native event frame limit exceeded")
                    self._fail("Native event connection disconnected")
        except asyncio.CancelledError:
            self._fail("Native event connection stopped")
            raise
        except Exception:
            # Do not expose response payloads, credentials, or model data in errors.
            self._fail("Native event observation failed")

    async def close(self) -> None:
        self._closed = True
        self._fail("Native event hub closed")
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

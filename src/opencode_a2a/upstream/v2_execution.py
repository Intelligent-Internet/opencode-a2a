"""Exclusive-profile execution for gateway-created native sessions.

API: register_created(session, directory) only for sessions created by the gateway;
submit(session, text) holds its lease through observed terminal and native settlement;
abort(session, expected_input_id) binds cancellation to that invocation and gates
later admission through interrupt settlement. Call next_update(session) for
V1-shaped text, reasoning and tool events and native intervention events with
``properties=data`` (the owner maps interventions before StreamRuntime). Completed
streams emit session.idle; failed/cancelled streams emit a typed terminal update.
Updates are opt-in with submit(..., emit_updates=True). No retries. Call
ensure_capacity() under the adapter creation lock before upstream session creation;
only fresh server-generated IDs may be passed to register_created().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import quote
from uuid import uuid4

from ..opencode_upstream_client import OpencodeUpstreamClient
from .contracts import (
    ExecutionOutcome,
    ExecutionState,
    ExecutionUpdate,
    PromptReceipt,
    PromptSubmission,
    SessionRef,
    UpdateKind,
)
from .v2_events import (
    NativeEvent,
    SessionSubscription,
    V2EventHub,
    V2ExecutionUncertainError,
    _object,
)

_TERMINALS = {
    "session.execution.succeeded": ExecutionState.COMPLETED,
    "session.execution.failed": ExecutionState.FAILED,
    "session.execution.interrupted": ExecutionState.CANCELLED,
}

V2StreamUpdate = dict[str, object] | ExecutionUpdate


@dataclass
class _Invocation:
    id: str
    event_ids: set[str] = field(default_factory=set)
    emit_updates: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)
    enqueued: bool = False
    delivered: bool = False
    aborting: bool = False
    assistants: list[str] = field(default_factory=list)
    terminal: NativeEvent | None = None
    submission: PromptSubmission | None = None
    tools: dict[str, dict[str, object]] = field(default_factory=dict)
    dispatched: bool = False


@dataclass
class _Session:
    directory: str
    subscription: SessionSubscription
    updates: asyncio.Queue[V2StreamUpdate]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    invocation: _Invocation | None = None
    abort_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class V2ExecutionEngine:
    def __init__(
        self,
        client: OpencodeUpstreamClient,
        hub: V2EventHub,
        *,
        timeout: float = 120,
        update_limit: int = 256,
        page_limit: int = 20,
        max_active: int | None = None,
    ) -> None:
        if timeout <= 0 or min(update_limit, page_limit) < 1:
            raise ValueError("Execution limits must be positive")
        self.client = client
        self.hub = hub
        self.timeout = timeout
        self.update_limit = update_limit
        self.page_limit = page_limit
        self.sessions: dict[str, _Session] = {}
        self.max_active = (
            client._settings.opencode_max_concurrent_requests if max_active is None else max_active
        )
        if self.max_active < 0:
            raise ValueError("Active execution limit must not be negative")
        self._active = 0

    def ensure_capacity(self) -> None:
        """Call under the adapter creation lock, before creating a fresh upstream session.

        Only healthy idle ownership is retired. Quarantined ownership remains bounded
        by the session limit and cannot be revived by session continuation.
        """
        self.hub.check()
        if len(self.hub.sessions) < self.hub.session_limit:
            return
        for session, state in self.sessions.items():
            if (
                state.invocation is None
                and not state.lock.locked()
                and not state.abort_lock.locked()
                and state.subscription.error is None
            ):
                self.hub.unregister(session)
                del self.sessions[session]
                return
        raise V2ExecutionUncertainError("Native event session capacity is occupied")

    def register_created(self, session: str, directory: str) -> None:
        """Register a freshly generated upstream ID, never an existing/continued ID."""
        subscription = self.hub.register(session)
        self.sessions[session] = _Session(directory, subscription, asyncio.Queue(self.update_limit))

    def reject_intervention(self, session: str) -> None:
        self._session(session).subscription.poison("Unsupported native intervention")

    def _session(self, session: str) -> _Session:
        state = self.sessions.get(session)
        if state is None:
            raise V2ExecutionUncertainError("Execution requires a gateway-created session")
        self.hub.check()
        state.subscription.check()
        # Dict order is the LRU order; there is no unbounded retired-ID registry.
        self.sessions.pop(session)
        self.sessions[session] = state
        return state

    def validate_intervention(
        self, session: str, *, assistant_message_id: str | None = None
    ) -> str:
        state = self._session(session)
        record = state.invocation
        if record is None or record.done.is_set() or record.terminal is not None or record.aborting:
            raise V2ExecutionUncertainError("Intervention requires an in-flight own invocation")
        if assistant_message_id is not None and assistant_message_id not in record.assistants:
            raise V2ExecutionUncertainError("Intervention belongs to an unknown assistant step")
        return record.id

    def invocation_id(self, session: str) -> str:
        record = self._session(session).invocation
        if record is None:
            raise V2ExecutionUncertainError("No owned invocation remains")
        # Cancellation may race a terminal whose projection is still settling.
        # Unlike a human reply, it must await that result rather than discard it.
        return record.id

    @staticmethod
    def _path(session: str) -> str:
        return f"/api/session/{quote(session, safe='')}"

    async def _get(self, path: str, state: _Session, **params: str) -> dict[str, object]:
        payload = await self.client._get_json(
            path,
            endpoint=path,
            params={"location[directory]": state.directory, **params},
        )
        return _object(payload)

    async def _idle(self, session: str, state: _Session) -> None:
        active = await self._get("/api/session/active", state)
        if session in _object(active.get("data")):
            raise V2ExecutionUncertainError("Session has unfinished or foreign activity")
        inbox = await self._get(f"{self._path(session)}/inbox", state)
        if inbox.get("data") != []:
            raise V2ExecutionUncertainError("Session inbox is not empty")

    def _emit(self, state: _Session, update: V2StreamUpdate) -> None:
        if state.invocation is None or not state.invocation.emit_updates:
            return
        try:
            state.updates.put_nowait(update)
        except asyncio.QueueFull as exc:
            state.subscription.poison("Execution update queue overflowed")
            raise V2ExecutionUncertainError("Execution update observation overflowed") from exc

    async def next_update(self, session: str) -> V2StreamUpdate:
        state = self._session(session)
        pending = asyncio.create_task(state.updates.get())
        failed = asyncio.create_task(state.subscription.failed.wait())
        try:
            await asyncio.wait((pending, failed), return_when=asyncio.FIRST_COMPLETED)
            state.subscription.check()
            return pending.result()
        finally:
            pending.cancel()
            failed.cancel()
            await asyncio.gather(pending, failed, return_exceptions=True)

    def _observe(
        self, session: str, state: _Session, record: _Invocation, event: NativeEvent
    ) -> None:
        if event.id in record.event_ids or len(record.event_ids) >= 4096:
            raise V2ExecutionUncertainError("Native invocation event identity budget violated")
        record.event_ids.add(event.id)
        kind = event.type
        data = event.data
        if kind.startswith("session.inbox."):
            if event.seq is None or data.get("inboxID") != record.id:
                raise V2ExecutionUncertainError("Observed foreign or unsequenced session input")
            if kind == "session.inbox.enqueued":
                item = _object(data.get("item"))
                if record.enqueued or item.get("type") != "user":
                    raise V2ExecutionUncertainError("Observed duplicate or synthetic session input")
                record.enqueued = True
                return
            if kind == "session.inbox.delivered":
                if not record.enqueued or record.delivered:
                    raise V2ExecutionUncertainError("Own input delivery order is uncertain")
                record.delivered = True
                return
            if kind == "session.inbox.cancelled" and record.aborting and not record.delivered:
                # Pending deletion is not execution interruption or completion evidence.
                return
            raise V2ExecutionUncertainError("Unexpected session inbox mutation")
        if kind in {
            "session.synthetic",
            "session.compacted",
            "session.moved",
            "session.deleted",
        } or kind.startswith("session.shell."):
            raise V2ExecutionUncertainError("Unexpected synthetic or external session work")
        if kind == "session.step.started":
            identity = data.get("assistantMessageID")
            if not record.delivered or event.seq is None or not isinstance(identity, str):
                raise V2ExecutionUncertainError("Assistant step has no observed own delivery")
            if identity not in record.assistants:
                record.assistants.append(identity)
            return
        identity = data.get("assistantMessageID")
        if identity is not None and identity not in record.assistants:
            raise V2ExecutionUncertainError("Observed unknown assistant step")
        if kind in _TERMINALS:
            if not record.delivered or not record.assistants or event.seq is None:
                raise V2ExecutionUncertainError("Terminal has no observed own assistant execution")
            if kind == "session.execution.failed" and not isinstance(data.get("error"), dict):
                raise V2ExecutionUncertainError("Failed terminal has no error record")
            if kind == "session.execution.interrupted" and (
                data.get("reason") != "user" or not record.aborting
            ):
                raise V2ExecutionUncertainError(
                    "Session interrupted by an unowned lifecycle change"
                )
            record.terminal = event
            return
        if kind in {
            "session.text.delta",
            "session.text.ended",
            "session.reasoning.started",
            "session.reasoning.delta",
            "session.reasoning.ended",
        }:
            if not isinstance(identity, str) or identity not in record.assistants:
                raise V2ExecutionUncertainError("Text has no observed assistant step")
            ordinal = data.get("ordinal")
            if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
                raise V2ExecutionUncertainError("Text part ordinal is missing")
            part_type = "reasoning" if kind.startswith("session.reasoning.") else "text"
            part = f"{identity}:{part_type}:{ordinal}"
            if kind.endswith(".delta"):
                delta = data.get("delta")
                if not isinstance(delta, str):
                    raise V2ExecutionUncertainError("Text delta is malformed")
                self._emit(
                    state,
                    {
                        "type": "message.part.delta",
                        "properties": {
                            "sessionID": session,
                            "messageID": identity,
                            "partID": part,
                            "field": "text",
                            "delta": delta,
                        },
                    },
                )
                return
            text = "" if kind.endswith(".started") else data.get("text")
            if not isinstance(text, str):
                raise V2ExecutionUncertainError("Text snapshot is malformed")
            self._emit(
                state,
                {
                    "type": "message.part.updated",
                    "properties": {
                        "part": {
                            "sessionID": session,
                            "messageID": identity,
                            "id": part,
                            "type": part_type,
                            "text": text,
                        }
                    },
                },
            )
            return
        if kind.startswith("session.tool."):
            self._tool(session, state, record, event)
            return
        if kind.startswith(("form.", "permission.")):
            if kind in {"form.created", "permission.asked"} and not record.emit_updates:
                raise V2ExecutionUncertainError(
                    "Unary interventions are unsupported; session quarantined without approval"
                )
            self._emit(state, {"type": kind, "properties": dict(data)})

    def _tool(self, session: str, state: _Session, record: _Invocation, event: NativeEvent) -> None:
        """Map the pinned Tool.Input/Called/Progress/Success/Failed schemas."""
        data = event.data
        identity = data.get("assistantMessageID")
        call = data.get("id")
        if not isinstance(identity, str) or identity not in record.assistants:
            raise V2ExecutionUncertainError("Tool has no observed assistant step")
        if not isinstance(call, str) or not call:
            raise V2ExecutionUncertainError("Tool call identity is missing")
        key = f"{identity}:tool:{call}"
        previous = record.tools.get(key)
        if event.type == "session.tool.input.started":
            name = data.get("name")
            if previous is not None or not isinstance(name, str) or not name:
                raise V2ExecutionUncertainError("Tool name is missing or call is duplicated")
            part: dict[str, object] = {
                "sessionID": session,
                "messageID": identity,
                "id": key,
                "type": "tool",
                "callID": call,
                "tool": name,
                "state": {"status": "pending", "input": {}},
            }
        else:
            if previous is None:
                raise V2ExecutionUncertainError("Tool event has no observed call start")
            part = dict(previous)
            toolstate = dict(_object(part["state"]))
            if toolstate.get("status") in {"completed", "error"}:
                raise V2ExecutionUncertainError("Tool changed after its terminal event")
            if event.type == "session.tool.called":
                toolstate.update(status="running", input=_object(data.get("input")))
            elif event.type == "session.tool.progress":
                toolstate["metadata"] = _object(data.get("metadata"))
            elif event.type in {"session.tool.success", "session.tool.failed"}:
                content = data.get("content")
                if event.type == "session.tool.success" or content is not None:
                    if not isinstance(content, list) or not content:
                        raise V2ExecutionUncertainError("Tool terminal content is malformed")
                    chunks = [_object(chunk) for chunk in content]
                    for chunk in chunks:
                        if chunk.get("type") == "text" and isinstance(chunk.get("text"), str):
                            continue
                        if (
                            chunk.get("type") == "file"
                            and isinstance(chunk.get("uri"), str)
                            and isinstance(chunk.get("mime"), str)
                        ):
                            continue
                        raise V2ExecutionUncertainError("Tool terminal content is malformed")
                    # Preserve file content alongside text in the structured artifact.
                    toolstate["output"] = chunks
                toolstate["status"] = (
                    "completed" if event.type == "session.tool.success" else "error"
                )
                if event.type == "session.tool.failed":
                    toolstate["error"] = _object(data.get("error"))
                if "metadata" in data:
                    toolstate["metadata"] = _object(data["metadata"])
            elif event.type in {"session.tool.input.delta", "session.tool.input.ended"}:
                return
            else:
                raise V2ExecutionUncertainError("Unknown native tool event")
            part["state"] = toolstate
        record.tools[key] = part
        self._emit(state, {"type": "message.part.updated", "properties": {"part": part}})

    async def submit(
        self, session: str, text: str, *, emit_updates: bool = False
    ) -> PromptSubmission:
        state = self._session(session)
        if not text.strip():
            raise ValueError("Prompt text must not be empty")
        async with state.lock:
            async with state.abort_lock:
                if self._session(session) is not state:
                    raise V2ExecutionUncertainError("Session ownership was retired")
                if self.max_active and self._active >= self.max_active:
                    raise V2ExecutionUncertainError("Active execution capacity is occupied")
                self._active += 1
            record = _Invocation(f"msg_{uuid4().hex}", emit_updates=emit_updates)
            try:
                async with asyncio.timeout(self.timeout):
                    self._session(session)
                    while not state.updates.empty():
                        state.updates.get_nowait()
                    await self.hub.start()
                    await self._idle(session, state)
                    state.subscription.check()
                    while not state.subscription.queue.empty():
                        baseline = state.subscription.queue.get_nowait()
                        if (
                            baseline.type != "session.created"
                            or baseline.data.get("sessionID") != session
                        ):
                            raise V2ExecutionUncertainError(
                                "Session changed outside its invocation lease"
                            )
                    state.invocation = record
                    path = f"{self._path(session)}/prompt"
                    record.dispatched = True
                    response = _object(
                        await self.client._post_json(
                            path,
                            endpoint=path,
                            params={"location[directory]": state.directory},
                            json_body={
                                "id": record.id,
                                "text": text,
                                "delivery": "queue",
                                "resume": True,
                            },
                        )
                    )
                    if _object(response.get("data")).get("id") != record.id:
                        raise V2ExecutionUncertainError(
                            "Prompt receipt does not match admitted input"
                        )
                    while record.terminal is None:
                        event = await state.subscription.receive()
                        self._observe(session, state, record, event)
                    submission = await self._settle(session, state, record)
                    state.subscription.check()
                    if not state.subscription.queue.empty():
                        raise V2ExecutionUncertainError(
                            "Session changed after its observed terminal"
                        )
                    if (
                        submission.outcome is not None
                        and submission.outcome.state == ExecutionState.COMPLETED
                    ):
                        self._emit(
                            state, {"type": "session.idle", "properties": {"sessionID": session}}
                        )
                    else:
                        self._emit(
                            state,
                            ExecutionUpdate(
                                UpdateKind.OTHER,
                                session_id=session,
                                terminal=submission.outcome,
                            ),
                        )
                    record.submission = submission
                    return submission
            except asyncio.CancelledError:
                state.subscription.poison("Invocation owner cancelled before proven settlement")
                raise
            except Exception as exc:
                state.subscription.poison("Invocation attribution or settlement is uncertain")
                if isinstance(exc, V2ExecutionUncertainError):
                    raise
                raise V2ExecutionUncertainError(
                    "Invocation failed without proven settlement; no retry"
                ) from exc
            finally:
                record.done.set()
                state.invocation = None
                # Uncertain dispatched work can still be running natively. Its
                # quarantined slot must not admit unbounded replacement work.
                if not record.dispatched or record.submission is not None:
                    self._active -= 1

    async def _settle(self, session: str, state: _Session, record: _Invocation) -> PromptSubmission:
        terminal = record.terminal
        if terminal is None:
            raise V2ExecutionUncertainError("Settlement requires an observed terminal")
        path = f"/api/experimental/session/{quote(session, safe='')}/wait"
        async with self.client._request_budget.reserve(operation=path):
            response = await self.client._client.post(
                path,
                params={"location[directory]": state.directory},
                headers=self.client._request_headers(),
            )
            response.raise_for_status()
            if response.status_code != 204:
                raise V2ExecutionUncertainError("Native wait did not settle")
        await self._idle(session, state)
        messages: dict[str, dict[str, object]] = {}
        cursor: str | None = None
        idle_id = "msg_" + terminal.id[4:] if terminal.id.startswith("evt_") else terminal.id
        required = {record.id, idle_id, *record.assistants}
        for _ in range(self.page_limit):
            params = {"cursor": cursor} if cursor else {"order": "desc"}
            page = await self._get(f"{self._path(session)}/message", state, limit="100", **params)
            items = page.get("data")
            if not isinstance(items, list) or len(items) > 100:
                raise V2ExecutionUncertainError("Message projection page is malformed")
            for raw in items:
                item = _object(raw)
                identity = item.get("id")
                if not isinstance(identity, str) or identity in messages:
                    raise V2ExecutionUncertainError(
                        "Message projection identity is duplicated or missing"
                    )
                messages[identity] = item
            if required <= messages.keys():
                break
            next_cursor = _object(page.get("cursor", {})).get("next")
            if not items or not isinstance(next_cursor, str) or next_cursor == cursor:
                raise V2ExecutionUncertainError("Exact invocation projection is missing")
            cursor = next_cursor
        if not required <= messages.keys():
            raise V2ExecutionUncertainError("Message projection page budget exhausted")
        outcome_name = terminal.type.rsplit(".", 1)[-1]
        if (
            messages[record.id].get("type") != "user"
            or messages[idle_id].get("type") != "idle"
            or messages[idle_id].get("outcome") != outcome_name
        ):
            raise V2ExecutionUncertainError("Persisted input or terminal outcome does not match")
        output: list[str] = []
        for identity in record.assistants:
            assistant = messages[identity]
            if assistant.get("type") != "assistant":
                raise V2ExecutionUncertainError("Observed step projection is not an assistant")
            content = assistant.get("content")
            if not isinstance(content, list):
                raise V2ExecutionUncertainError("Observed assistant content is missing")
            for raw in content:
                part = _object(raw)
                if part.get("type") == "text":
                    text = part.get("text")
                    if not isinstance(text, str):
                        raise V2ExecutionUncertainError("Persisted assistant text is malformed")
                    output.append(text)
        outcome = ExecutionOutcome(
            SessionRef(session),
            _TERMINALS[terminal.type],
            "\n".join(output),
            message_id=record.assistants[-1],
            error_type="upstream_execution_failed" if outcome_name == "failed" else None,
            error_message="Upstream execution failed" if outcome_name == "failed" else None,
        )
        return PromptSubmission(PromptReceipt(SessionRef(session), record.id), outcome)

    async def abort(self, session: str, expected_input_id: str | None = None) -> PromptSubmission:
        state = self._session(session)
        record = state.invocation
        if (
            record is None
            or record.aborting
            or expected_input_id is not None
            and record.id != expected_input_id
        ):
            raise V2ExecutionUncertainError("Abort requires a known in-flight own invocation")
        record.aborting = True
        # Acquire synchronously before yielding: natural settlement may release the
        # invocation lease while the session-wide interrupt HTTP request is in flight.
        await state.abort_lock.acquire()
        try:
            async with asyncio.timeout(self.timeout):
                path = self._path(session)
                if not record.delivered:
                    deletion = f"{path}/inbox/{quote(record.id, safe='')}"
                    async with self.client._request_budget.reserve(operation=deletion):
                        if not record.done.is_set() and record.terminal is None:
                            response = await self.client._client.delete(
                                deletion,
                                params={"location[directory]": state.directory},
                                headers=self.client._request_headers(),
                            )
                            response.raise_for_status()
                            if response.status_code != 204:
                                raise V2ExecutionUncertainError(
                                    "Pending input deletion did not settle"
                                )
                async with self.client._request_budget.reserve(operation=f"{path}/interrupt"):
                    self.hub.check()
                    state.subscription.check()
                    if not record.done.is_set() and record.terminal is None:
                        if state.invocation is not record:
                            raise V2ExecutionUncertainError("Abort invocation ownership changed")
                        response = await self.client._client.post(
                            f"{path}/interrupt",
                            params={"resume": "false", "location[directory]": state.directory},
                            headers=self.client._request_headers(),
                        )
                        response.raise_for_status()
                        response_data = _object(response.json())
                        if not isinstance(response_data.get("interrupted"), bool):
                            raise V2ExecutionUncertainError("Native interrupt receipt is malformed")
                await record.done.wait()
                state.subscription.check()
                if record.submission is None:
                    raise V2ExecutionUncertainError("Abort has no observed settled outcome")
                return record.submission
        except asyncio.CancelledError:
            state.subscription.poison("Abort did not prove invocation settlement")
            raise
        except Exception as exc:
            state.subscription.poison("Abort did not prove invocation settlement")
            if isinstance(exc, V2ExecutionUncertainError):
                raise
            raise V2ExecutionUncertainError(
                "Abort failed without proven settlement; no retry"
            ) from exc
        finally:
            state.abort_lock.release()

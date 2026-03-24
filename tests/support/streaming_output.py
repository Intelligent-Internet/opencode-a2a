import asyncio

from a2a.types import (
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

from opencode_a2a.opencode_upstream_client import OpencodeMessage
from tests.support.helpers import (
    DummyEventQueue,
    make_settings,
)


class DummyStreamingClient:
    def __init__(
        self,
        *,
        stream_events_payload: list[dict],
        response_text: str,
        response_message_id: str | None = "msg-1",
        response_raw: dict | None = None,
        send_delay: float = 0.02,
        stream_event_delays: list[float] | None = None,
        auto_idle: bool = True,
    ) -> None:
        self._stream_events_payload = stream_events_payload
        self._response_text = response_text
        self._response_message_id = response_message_id
        self._response_raw = response_raw or {}
        self._send_delay = send_delay
        self._stream_event_delays = stream_event_delays or []
        self._auto_idle = auto_idle
        self._in_flight_send = 0
        self.max_in_flight_send = 0
        self.stream_timeout = None
        self.directory = None
        self._interrupt_sessions: dict[str, str] = {}
        self.settings = make_settings(
            a2a_bearer_token="test",
            opencode_base_url="http://localhost",
        )

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        return "ses-1"

    async def send_message(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: list[dict] | None = None,
        directory: str | None = None,
        model_override: dict[str, str] | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del text, parts, directory, model_override, timeout_override
        self._in_flight_send += 1
        self.max_in_flight_send = max(self.max_in_flight_send, self._in_flight_send)
        await asyncio.sleep(self._send_delay)
        self._in_flight_send -= 1
        return OpencodeMessage(
            text=self._response_text,
            session_id=session_id,
            message_id=self._response_message_id,
            raw=self._response_raw,
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del directory
        for index, event in enumerate(self._stream_events_payload):
            if stop_event and stop_event.is_set():
                break
            delay = (
                self._stream_event_delays[index] if index < len(self._stream_event_delays) else 0
            )
            await asyncio.sleep(delay)
            yield event
        if self._auto_idle and not any(
            event.get("type") in {"session.idle", "session.error"}
            for event in self._stream_events_payload
        ):
            yield {"type": "session.idle", "properties": {"sessionID": "ses-1"}}

    async def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str | None = None,
        identity: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        del interrupt_type, identity, task_id, context_id, ttl_seconds
        self._interrupt_sessions[request_id] = session_id

    async def resolve_interrupt_session(self, request_id: str) -> str | None:
        return self._interrupt_sessions.get(request_id)

    async def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_sessions.pop(request_id, None)


def _event(
    *,
    session_id: str,
    role: str | None,
    part_type: str,
    delta: str,
    message_id: str | None = "msg-1",
    part_id: str | None = None,
    text: str | None = None,
    part_overrides: dict | None = None,
) -> dict:
    resolved_part_id = part_id or f"prt-{message_id or 'missing'}-{part_type}"
    properties: dict = {
        "part": {
            "id": resolved_part_id,
            "sessionID": session_id,
            "type": part_type,
        },
        "delta": delta,
    }
    if role is not None:
        properties["part"]["role"] = role
    if message_id is not None:
        properties["part"]["messageID"] = message_id
    if text is not None:
        properties["part"]["text"] = text
    if part_overrides:
        properties["part"].update(part_overrides)
    return {
        "type": "message.part.updated",
        "properties": properties,
    }


def _delta_event(
    *,
    session_id: str,
    part_id: str,
    delta: str,
    message_id: str | None = "msg-1",
) -> dict:
    properties: dict = {
        "sessionID": session_id,
        "partID": part_id,
        "field": "text",
        "delta": delta,
    }
    if message_id is not None:
        properties["messageID"] = message_id
    return {
        "type": "message.part.delta",
        "properties": properties,
    }


def _step_finish_usage_event(
    *,
    session_id: str,
    message_id: str = "msg-1",
    part_id: str = "prt-step-finish",
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost: float,
) -> dict:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {
                "id": part_id,
                "sessionID": session_id,
                "messageID": message_id,
                "type": "step-finish",
                "reason": "stop",
                "cost": cost,
                "tokens": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": total_tokens,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            }
        },
    }


def _permission_asked_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "permission.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "permission": "read",
            "patterns": ["/data/project/.env.secret"],
            "always": ["/data/project/.env.example"],
            "metadata": {"path": "/data/project/.env.secret"},
            "tool": {"messageID": "msg-tool-1", "callID": "call-tool-1"},
        },
    }


def _question_asked_event(*, session_id: str, request_id: str) -> dict:
    return {
        "type": "question.asked",
        "properties": {
            "id": request_id,
            "sessionID": session_id,
            "questions": [
                {
                    "header": "Confirm",
                    "question": "Proceed?",
                    "options": [{"label": "Yes", "value": "yes"}],
                }
            ],
        },
    }


def _interrupt_resolved_event(*, session_id: str, request_id: str, event_type: str) -> dict:
    return {
        "type": event_type,
        "properties": {
            "requestID": request_id,
            "sessionID": session_id,
        },
    }


def _artifact_updates(queue: DummyEventQueue) -> list[TaskArtifactUpdateEvent]:
    return [event for event in queue.events if isinstance(event, TaskArtifactUpdateEvent)]


def _part_text(event: TaskArtifactUpdateEvent) -> str:
    part = event.artifact.parts[0]
    return getattr(part, "text", None) or getattr(part.root, "text", "")


def _part_data(event: TaskArtifactUpdateEvent) -> dict:
    part = event.artifact.parts[0]
    return getattr(part, "data", None) or getattr(part.root, "data", {})


def _artifact_stream_meta(event: TaskArtifactUpdateEvent) -> dict:
    return event.artifact.metadata["shared"]["stream"]


def _status_shared_meta(event: TaskStatusUpdateEvent) -> dict:
    return (event.metadata or {})["shared"]


def _interrupt_meta(event: TaskStatusUpdateEvent) -> dict:
    return _status_shared_meta(event)["interrupt"]


def _progress_meta(event: TaskStatusUpdateEvent) -> dict:
    return _status_shared_meta(event)["progress"]


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered

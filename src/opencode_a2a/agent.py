from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)


class _TTLSessions:
    """Bounded TTL cache for context_id -> session_id.

    This is intentionally tiny and dependency-free. It provides best-effort cleanup:
    - Expired entries are removed on get/set.
    - When maxsize is exceeded, we evict expired entries first, then arbitrary entries.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int,
        maxsize: int,
        now: callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = int(ttl_seconds)
        self._maxsize = int(maxsize)
        self._now = now
        # value: (session_id, expires_at_monotonic)
        self._store: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        if expires_at <= self._now():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str) -> None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return
        now = self._now()
        expires_at = now + float(self._ttl_seconds)
        self._store[key] = (value, expires_at)
        self._evict_if_needed(now=now)

    def pop(self, key: str) -> None:
        self._store.pop(key, None)

    def _evict_if_needed(self, *, now: float) -> None:
        if len(self._store) <= self._maxsize:
            return
        # 1) Drop expired.
        expired = [k for k, (_, exp) in self._store.items() if exp <= now]
        for k in expired:
            self._store.pop(k, None)
        if len(self._store) <= self._maxsize:
            return
        # 2) Still too big: drop arbitrary entries to cap memory usage.
        overflow = len(self._store) - self._maxsize
        for k in list(self._store.keys())[:overflow]:
            self._store.pop(k, None)


class OpencodeAgentExecutor(AgentExecutor):
    def __init__(
        self,
        client: OpencodeClient,
        *,
        streaming_enabled: bool,
        session_cache_ttl_seconds: int = 3600,
        session_cache_maxsize: int = 10_000,
    ) -> None:
        self._client = client
        self._streaming_enabled = streaming_enabled
        self._sessions = _TTLSessions(
            ttl_seconds=session_cache_ttl_seconds,
            maxsize=session_cache_maxsize,
        )
        self._lock = asyncio.Lock()
        self._inflight_session_creates: dict[str, asyncio.Task[str]] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise RuntimeError("Missing task_id or context_id in request context")

        streaming_request = self._should_stream(context)
        user_text = context.get_user_input().strip()
        bound_session_id = _extract_opencode_session_id(context)

        if not user_text:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Only text input is supported.",
                streaming_request=streaming_request,
            )
            return

        logger.debug(
            "Received message task_id=%s context_id=%s streaming=%s text=%s",
            task_id,
            context_id,
            streaming_request,
            user_text,
        )

        session_id = await self._get_or_create_session(
            context_id,
            user_text,
            preferred_session_id=bound_session_id,
        )

        stream_artifact_id = f"{task_id}:stream"
        stop_event = asyncio.Event()
        stream_task: asyncio.Task[None] | None = None
        if streaming_request:
            stream_task = asyncio.create_task(
                self._consume_opencode_stream(
                    session_id=session_id,
                    task_id=task_id,
                    context_id=context_id,
                    artifact_id=stream_artifact_id,
                    event_queue=event_queue,
                    stop_event=stop_event,
                )
            )

        try:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.working),
                    final=False,
                )
            )
            if streaming_request:
                response = await self._client.send_message(
                    session_id,
                    user_text,
                    timeout_override=self._client.stream_timeout,
                )
            else:
                response = await self._client.send_message(session_id, user_text)
            response_text = response.text or "(No text content returned by OpenCode.)"
            logger.debug(
                "OpenCode response task_id=%s session_id=%s message_id=%s text=%s",
                task_id,
                response.session_id,
                response.message_id,
                response_text,
            )
            assistant_message = _build_assistant_message(
                task_id=task_id,
                context_id=context_id,
                text=response_text,
                message_id=response.message_id,
            )
            if streaming_request:
                await _enqueue_artifact_update(
                    event_queue=event_queue,
                    task_id=task_id,
                    context_id=context_id,
                    artifact_id=stream_artifact_id,
                    text=response_text,
                    append=False,
                    last_chunk=True,
                )
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(
                            state=TaskState.input_required,
                        ),
                        final=True,
                        metadata={
                            "opencode": {
                                "session_id": response.session_id,
                                "message_id": response.message_id,
                            }
                        },
                    )
                )
            else:
                artifact = Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="response",
                    parts=[TextPart(text=response_text)],
                )
                history = _build_history(context)
                task = Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.input_required),
                    history=history,
                    artifacts=[artifact],
                    metadata={
                        "opencode": {
                            "session_id": response.session_id,
                            "message_id": response.message_id,
                        }
                    },
                )
                # Attach the assistant message as the current status message.
                task.status.message = assistant_message
                await event_queue.enqueue_event(task)
        except Exception as exc:
            logger.exception("OpenCode request failed")
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=f"OpenCode error: {exc}",
                streaming_request=streaming_request,
            )
        finally:
            stop_event.set()
            if stream_task:
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise RuntimeError("Missing task_id or context_id in request context")

        event = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.canceled),
            final=True,
        )
        await event_queue.enqueue_event(event)
        await event_queue.close()
        async with self._lock:
            self._sessions.pop(context_id)
            inflight = self._inflight_session_creates.pop(context_id, None)
        if inflight:
            inflight.cancel()
            with suppress(asyncio.CancelledError):
                await inflight

    async def _get_or_create_session(
        self,
        context_id: str,
        title: str,
        *,
        preferred_session_id: str | None = None,
    ) -> str:
        # Caller explicitly bound the request to a known OpenCode session.
        if preferred_session_id:
            async with self._lock:
                self._sessions.set(context_id, preferred_session_id)
            return preferred_session_id

        task: asyncio.Task[str] | None = None
        async with self._lock:
            existing = self._sessions.get(context_id)
            if existing:
                return existing
            task = self._inflight_session_creates.get(context_id)
            if task is None:
                task = asyncio.create_task(self._client.create_session(title=title))
                self._inflight_session_creates[context_id] = task

        try:
            session_id = await task
        except Exception:
            async with self._lock:
                if self._inflight_session_creates.get(context_id) is task:
                    self._inflight_session_creates.pop(context_id, None)
            raise

        async with self._lock:
            # Session create finished; commit to cache and drop inflight marker.
            self._sessions.set(context_id, session_id)
            if self._inflight_session_creates.get(context_id) is task:
                self._inflight_session_creates.pop(context_id, None)
        return session_id

    async def _emit_error(
        self,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        message: str,
        *,
        streaming_request: bool,
    ) -> None:
        error_message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.agent,
            parts=[TextPart(text=message)],
            task_id=task_id,
            context_id=context_id,
        )
        if streaming_request:
            await _enqueue_artifact_update(
                event_queue=event_queue,
                task_id=task_id,
                context_id=context_id,
                artifact_id=f"{task_id}:error",
                text=message,
                append=False,
                last_chunk=True,
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.failed),
                    final=True,
                )
            )
            return
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.failed, message=error_message),
            history=[error_message],
        )
        await event_queue.enqueue_event(task)

    def _should_stream(self, context: RequestContext) -> bool:
        if not self._streaming_enabled:
            return False
        call_context = context.call_context
        if not call_context:
            return False
        if call_context.state.get("a2a_streaming_request"):
            return True
        # JSON-RPC transport sets method in call context state.
        method = call_context.state.get("method")
        return method == "message/stream"

    async def _consume_opencode_stream(
        self,
        *,
        session_id: str,
        task_id: str,
        context_id: str,
        artifact_id: str,
        event_queue: EventQueue,
        stop_event: asyncio.Event,
    ) -> None:
        buffered_text = ""
        backoff = 0.5
        max_backoff = 5.0
        sent_chunk = False
        try:
            while not stop_event.is_set():
                try:
                    async for event in self._client.stream_events(stop_event=stop_event):
                        if stop_event.is_set():
                            break
                        event_type = event.get("type")
                        if event_type != "message.part.updated":
                            continue
                        props = event.get("properties", {})
                        part = props.get("part") or {}
                        if part.get("sessionID") != session_id:
                            continue
                        role = part.get("role") or props.get("role")
                        if role is None:
                            message = props.get("message")
                            if isinstance(message, dict):
                                role = message.get("role")
                        if isinstance(role, str) and role.lower() in {"user", "system"}:
                            continue
                        delta = props.get("delta")
                        chunk_text: str | None = None
                        append = True
                        if isinstance(delta, str) and delta:
                            chunk_text = delta
                            buffered_text += delta
                        elif part.get("type") == "text" and isinstance(part.get("text"), str):
                            next_text = part["text"]
                            if next_text != buffered_text:
                                if next_text.startswith(buffered_text):
                                    chunk_text = next_text[len(buffered_text) :]
                                    append = True
                                else:
                                    chunk_text = next_text
                                    append = False
                                buffered_text = next_text
                        if not chunk_text:
                            continue
                        if not sent_chunk:
                            append = False
                            sent_chunk = True
                        await _enqueue_artifact_update(
                            event_queue=event_queue,
                            task_id=task_id,
                            context_id=context_id,
                            artifact_id=artifact_id,
                            text=chunk_text,
                            append=append,
                            last_chunk=False,
                        )
                        logger.debug(
                            "Stream chunk task_id=%s session_id=%s append=%s text=%s",
                            task_id,
                            session_id,
                            append,
                            chunk_text,
                        )
                    break
                except Exception:
                    if stop_event.is_set():
                        break
                    logger.exception("OpenCode event stream failed; retrying")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
        except Exception:
            logger.exception("OpenCode event stream failed")


def _build_assistant_message(
    task_id: str,
    context_id: str,
    text: str,
    *,
    message_id: str | None = None,
) -> Message:
    return Message(
        message_id=message_id or str(uuid.uuid4()),
        role=Role.agent,
        parts=[TextPart(text=text)],
        task_id=task_id,
        context_id=context_id,
    )


async def _enqueue_artifact_update(
    *,
    event_queue: EventQueue,
    task_id: str,
    context_id: str,
    artifact_id: str,
    text: str,
    append: bool | None,
    last_chunk: bool | None,
) -> None:
    artifact = Artifact(
        artifact_id=artifact_id,
        parts=[TextPart(text=text)],
    )
    await event_queue.enqueue_event(
        TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=append,
            last_chunk=last_chunk,
        )
    )


def _build_history(context: RequestContext) -> list[Message]:
    if context.current_task and context.current_task.history:
        history = list(context.current_task.history)
    else:
        history = []
        if context.message:
            history.append(context.message)
    # Do not append assistant message to history; it lives in status.message.
    return history


def _extract_opencode_session_id(context: RequestContext) -> str | None:
    # Contract: clients may pass the binding at either request-level metadata
    # (MessageSendParams.metadata) or message-level metadata (Message.metadata).
    candidate = None
    try:
        meta = context.metadata or {}
        candidate = meta.get("opencode_session_id")
    except Exception:
        candidate = None

    if not candidate and context.message is not None:
        msg_meta = getattr(context.message, "metadata", None) or {}
        if isinstance(msg_meta, dict):
            candidate = msg_meta.get("opencode_session_id")

    if isinstance(candidate, str):
        value = candidate.strip()
        return value or None
    return None

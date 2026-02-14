from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

_STREAM_CHANNEL_REASONING = "reasoning"
_STREAM_CHANNEL_TOOL_CALL = "tool_call"
_STREAM_CHANNEL_FINAL_ANSWER = "final_answer"


@dataclass(frozen=True)
class _NormalizedStreamChunk:
    text: str
    append: bool
    channel: str
    source: str
    event_type: str
    message_id: str | None
    role: str | None


@dataclass
class _StreamOutputState:
    user_text: str
    response_message_id: str | None = None
    channel_buffers: dict[str, str] = field(default_factory=dict)
    saw_final_answer_chunk: bool = False
    saw_any_chunk: bool = False

    def set_response_message_id(self, message_id: str | None) -> None:
        if not isinstance(message_id, str):
            self.response_message_id = None
            return
        value = message_id.strip()
        self.response_message_id = value or None

    def matches_expected_message(self, message_id: str | None) -> bool:
        if not message_id:
            return False
        if not self.response_message_id:
            return True
        return message_id == self.response_message_id

    def should_drop_initial_user_echo(self, text: str, *, channel: str, role: str | None) -> bool:
        if role is not None:
            return False
        if channel != _STREAM_CHANNEL_FINAL_ANSWER:
            return False
        if self.saw_any_chunk:
            return False
        user_text = self.user_text.strip()
        return bool(user_text) and text.strip() == user_text

    def register_chunk(self, *, channel: str, text: str, append: bool) -> tuple[bool, bool]:
        previous = self.channel_buffers.get(channel, "")
        effective_append = append if previous else False
        next_value = f"{previous}{text}" if effective_append else text
        if next_value == previous:
            return False, effective_append
        self.channel_buffers[channel] = next_value
        self.saw_any_chunk = True
        if channel == _STREAM_CHANNEL_FINAL_ANSWER and next_value.strip():
            self.saw_final_answer_chunk = True
        return True, effective_append

    def should_emit_final_snapshot(self, text: str) -> bool:
        if not text.strip():
            return False
        existing = self.channel_buffers.get(_STREAM_CHANNEL_FINAL_ANSWER, "")
        if existing.strip() == text.strip():
            return False
        self.channel_buffers[_STREAM_CHANNEL_FINAL_ANSWER] = text
        self.saw_any_chunk = True
        self.saw_final_answer_chunk = True
        return True


class _TTLCache:
    """Bounded TTL cache for hashable key -> string value.

    This is intentionally tiny and dependency-free. It provides best-effort cleanup:
    - Expired entries are removed on get/set.
    - When maxsize is exceeded, we evict expired entries first, then the earliest-expiring entries.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int,
        maxsize: int,
        now: callable[[], float] = time.monotonic,
        refresh_on_get: bool = False,
    ) -> None:
        self._ttl_seconds = int(ttl_seconds)
        self._maxsize = int(maxsize)
        self._now = now
        self._refresh_on_get = bool(refresh_on_get)
        # value: (string_value, expires_at_monotonic)
        self._store: dict[object, tuple[str, float]] = {}

    def get(self, key: object) -> str | None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        now = self._now()
        if expires_at <= now:
            self._store.pop(key, None)
            return None
        if self._refresh_on_get:
            self._store[key] = (value, now + float(self._ttl_seconds))
        return value

    def set(self, key: object, value: str) -> None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return
        now = self._now()
        expires_at = now + float(self._ttl_seconds)
        self._store[key] = (value, expires_at)
        self._evict_if_needed(now=now)

    def pop(self, key: object) -> None:
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
        # 2) Still too big: evict the least recently renewed entries first.
        overflow = len(self._store) - self._maxsize
        by_expiry = sorted(self._store.items(), key=lambda item: item[1][1])
        for k, _ in by_expiry[:overflow]:
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
        self._sessions = _TTLCache(
            ttl_seconds=session_cache_ttl_seconds,
            maxsize=session_cache_maxsize,
        )
        self._session_owners = _TTLCache(
            ttl_seconds=session_cache_ttl_seconds,
            maxsize=session_cache_maxsize,
            refresh_on_get=True,
        )  # session_id -> identity
        self._pending_session_claims: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._inflight_session_creates: dict[tuple[str, str], asyncio.Task[str]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._running_requests: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._running_stop_events: dict[tuple[str, str], asyncio.Event] = {}
        self._running_identities: dict[tuple[str, str], str] = {}

    def _resolve_and_validate_directory(self, requested: str | None) -> str | None:
        """Normalizes and validates the directory parameter against workspace boundaries.

        Returns:
            The normalized absolute path string if valid.
        Raises:
            ValueError: If the path is outside the allowed workspace.
        """
        base_dir_str = self._client.directory or os.getcwd()
        base_path = Path(base_dir_str).resolve()

        if requested is not None and not isinstance(requested, str):
            raise ValueError("Directory must be a string path")

        requested = requested.strip() if requested else requested
        if not requested:
            return str(base_path)

        def _resolve_requested(path: str) -> Path:
            p = Path(path)
            if not p.is_absolute():
                p = base_path / p
            return p.resolve()

        # 1. Deny override if disabled in settings
        if not self._client.settings.a2a_allow_directory_override:
            # If requested matches normalized base, it's fine.
            requested_path = _resolve_requested(requested)
            if requested_path == base_path:
                return str(base_path)
            raise ValueError("Directory override is disabled by service configuration")

        # 2. Resolve requested path
        requested_path = _resolve_requested(requested)

        # 3. Boundary check: must be subpath of base_path
        try:
            requested_path.relative_to(base_path)
        except ValueError as err:
            raise ValueError(
                f"Directory {requested} is outside the allowed workspace {base_path}"
            ) from err

        return str(requested_path)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            await self._emit_error(
                event_queue,
                task_id=task_id or "unknown",
                context_id=context_id or "unknown",
                message="Missing task_id or context_id in request context",
                streaming_request=self._should_stream(context),
            )
            return

        call_context = context.call_context
        identity = (call_context.state.get("identity") if call_context else None) or "anonymous"

        streaming_request = self._should_stream(context)
        user_text = context.get_user_input().strip()
        bound_session_id = _extract_opencode_session_id(context)

        # Directory validation
        requested_dir = None
        metadata = context.metadata
        if metadata is not None and not isinstance(metadata, Mapping):
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Invalid metadata: expected an object/map.",
                streaming_request=streaming_request,
            )
            return
        if isinstance(metadata, Mapping):
            requested_dir = metadata.get("directory")

        try:
            directory = self._resolve_and_validate_directory(requested_dir)
        except ValueError as e:
            logger.warning("Directory validation failed: %s", e)
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=str(e),
                streaming_request=streaming_request,
            )
            return

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
            "Received message identity=%s task_id=%s context_id=%s streaming=%s text=%s",
            identity,
            task_id,
            context_id,
            streaming_request,
            user_text,
        )

        stream_artifact_id = f"{task_id}:stream"
        stream_state = _StreamOutputState(user_text=user_text)
        stop_event = asyncio.Event()
        stream_task: asyncio.Task[None] | None = None
        pending_preferred_claim = False
        session_lock: asyncio.Lock | None = None
        session_id = ""
        execution_key = (task_id, context_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            async with self._lock:
                self._running_requests[execution_key] = current_task
                self._running_stop_events[execution_key] = stop_event
                self._running_identities[execution_key] = identity

        try:
            session_id, pending_preferred_claim = await self._get_or_create_session(
                identity,
                context_id,
                user_text,
                preferred_session_id=bound_session_id,
                directory=directory,
            )
            session_lock = await self._get_session_lock(session_id)
            await session_lock.acquire()

            if streaming_request:
                stream_task = asyncio.create_task(
                    self._consume_opencode_stream(
                        session_id=session_id,
                        task_id=task_id,
                        context_id=context_id,
                        artifact_id=stream_artifact_id,
                        stream_state=stream_state,
                        event_queue=event_queue,
                        stop_event=stop_event,
                        directory=directory,
                    )
                )

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.working),
                    final=False,
                )
            )
            send_kwargs: dict[str, str | float | None] = {"directory": directory}
            if streaming_request:
                send_kwargs["timeout_override"] = self._client.stream_timeout
            response = await self._client.send_message(
                session_id,
                user_text,
                **send_kwargs,
            )

            if pending_preferred_claim:
                await self._finalize_preferred_session_binding(
                    identity=identity,
                    context_id=context_id,
                    session_id=session_id,
                )
                pending_preferred_claim = False

            response_text = response.text or ""
            logger.debug(
                "OpenCode response task_id=%s session_id=%s message_id=%s text=%s",
                task_id,
                response.session_id,
                response.message_id,
                response_text,
            )
            if streaming_request:
                stream_state.set_response_message_id(response.message_id)
                if stream_state.should_emit_final_snapshot(response_text):
                    await _enqueue_artifact_update(
                        event_queue=event_queue,
                        task_id=task_id,
                        context_id=context_id,
                        artifact_id=_artifact_id_for_stream_channel(
                            stream_artifact_id, _STREAM_CHANNEL_FINAL_ANSWER
                        ),
                        text=response_text,
                        append=False,
                        last_chunk=True,
                        artifact_metadata=_build_stream_artifact_metadata(
                            channel=_STREAM_CHANNEL_FINAL_ANSWER,
                            source="final_snapshot",
                            event_type="message.finalized",
                            message_id=response.message_id,
                        ),
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
                response_text = response_text or "(No text content returned by OpenCode.)"
                assistant_message = _build_assistant_message(
                    task_id=task_id,
                    context_id=context_id,
                    text=response_text,
                    message_id=response.message_id,
                )
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
            if pending_preferred_claim and session_id:
                with suppress(Exception):
                    await self._release_preferred_session_claim(
                        identity=identity,
                        session_id=session_id,
                    )
            stop_event.set()
            if stream_task:
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
            if session_lock and session_lock.locked():
                session_lock.release()
            async with self._lock:
                self._running_requests.pop(execution_key, None)
                self._running_stop_events.pop(execution_key, None)
                self._running_identities.pop(execution_key, None)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        try:
            if not task_id or not context_id:
                await self._emit_error(
                    event_queue,
                    task_id=task_id or "unknown",
                    context_id=context_id or "unknown",
                    message="Missing task_id or context_id in request context",
                    streaming_request=False,
                )
                return

            call_context = context.call_context
            identity = (call_context.state.get("identity") if call_context else None) or "anonymous"

            event = TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.canceled),
                final=True,
            )
            await event_queue.enqueue_event(event)

            execution_key = (task_id, context_id)
            async with self._lock:
                running_identity = self._running_identities.get(execution_key, identity)
                running_task = self._running_requests.get(execution_key)
                stop_event = self._running_stop_events.get(execution_key)
                self._sessions.pop((running_identity, context_id))
                inflight = self._inflight_session_creates.pop((running_identity, context_id), None)
            if stop_event:
                stop_event.set()
            if (
                running_task
                and running_task is not asyncio.current_task()
                and not running_task.done()
            ):
                running_task.cancel()
            if inflight:
                inflight.cancel()
                with suppress(asyncio.CancelledError):
                    await inflight
        except Exception as exc:
            logger.exception("Cancel failed")
            if task_id and context_id:
                with suppress(Exception):
                    await self._emit_error(
                        event_queue,
                        task_id=task_id,
                        context_id=context_id,
                        message=f"Cancel failed: {exc}",
                        streaming_request=False,
                    )

    async def _get_or_create_session(
        self,
        identity: str,
        context_id: str,
        title: str,
        *,
        preferred_session_id: str | None = None,
        directory: str | None = None,
    ) -> tuple[str, bool]:
        # Caller explicitly bound the request to a known OpenCode session.
        if preferred_session_id:
            async with self._lock:
                owner = self._session_owners.get(preferred_session_id)
                pending_owner = self._pending_session_claims.get(preferred_session_id)
                if owner and owner != identity:
                    logger.warning(
                        "Identity %s tried to hijack session %s owned by %s",
                        identity,
                        preferred_session_id,
                        owner,
                    )
                    raise PermissionError(f"Session {preferred_session_id} is not owned by you")

                if pending_owner and pending_owner != identity:
                    logger.warning(
                        "Identity %s tried to use session %s while pending owner is %s",
                        identity,
                        preferred_session_id,
                        pending_owner,
                    )
                    raise PermissionError(f"Session {preferred_session_id} is not owned by you")

                # Existing owner is trusted and can be bound immediately.
                if owner == identity:
                    self._sessions.set((identity, context_id), preferred_session_id)
                    return preferred_session_id, False

                # Unknown owner: reserve a temporary claim; finalize after upstream send succeeds.
                self._pending_session_claims[preferred_session_id] = identity
                return preferred_session_id, True

        task: asyncio.Task[str] | None = None
        cache_key = (identity, context_id)
        async with self._lock:
            existing = self._sessions.get(cache_key)
            if existing:
                return existing, False
            task = self._inflight_session_creates.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._client.create_session(title=title, directory=directory)
                )
                self._inflight_session_creates[cache_key] = task

        try:
            session_id = await task
        except Exception:
            async with self._lock:
                if self._inflight_session_creates.get(cache_key) is task:
                    self._inflight_session_creates.pop(cache_key, None)
            raise

        async with self._lock:
            # Session create finished; commit to cache and drop inflight marker.
            owner = self._session_owners.get(session_id)
            if owner and owner != identity:
                if self._inflight_session_creates.get(cache_key) is task:
                    self._inflight_session_creates.pop(cache_key, None)
                raise PermissionError(f"Session {session_id} is not owned by you")
            self._sessions.set(cache_key, session_id)
            if not owner:
                self._session_owners.set(session_id, identity)
            if self._inflight_session_creates.get(cache_key) is task:
                self._inflight_session_creates.pop(cache_key, None)
        return session_id, False

    async def _finalize_preferred_session_binding(
        self,
        *,
        identity: str,
        context_id: str,
        session_id: str,
    ) -> None:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            pending_owner = self._pending_session_claims.get(session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")

            self._session_owners.set(session_id, identity)
            self._sessions.set((identity, context_id), session_id)
            if self._pending_session_claims.get(session_id) == identity:
                self._pending_session_claims.pop(session_id, None)

    async def _release_preferred_session_claim(self, *, identity: str, session_id: str) -> None:
        async with self._lock:
            if self._pending_session_claims.get(session_id) == identity:
                self._pending_session_claims.pop(session_id, None)

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

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
        stream_state: _StreamOutputState,
        event_queue: EventQueue,
        stop_event: asyncio.Event,
        directory: str | None = None,
    ) -> None:
        buffered_text: dict[str, str] = {}
        backoff = 0.5
        max_backoff = 5.0
        try:
            while not stop_event.is_set():
                try:
                    async for event in self._client.stream_events(
                        stop_event=stop_event, directory=directory
                    ):
                        if stop_event.is_set():
                            break
                        event_type = event.get("type")
                        if event_type != "message.part.updated":
                            continue
                        props = event.get("properties")
                        if not isinstance(props, Mapping):
                            continue
                        part = props.get("part")
                        if not isinstance(part, Mapping):
                            part = {}
                        if _extract_stream_session_id(part, props) != session_id:
                            continue
                        role = _extract_stream_role(part, props)
                        if role in {"user", "system"}:
                            continue
                        channel = _classify_stream_channel(part, props)
                        delta = props.get("delta")
                        message_id = _extract_stream_message_id(part, props)

                        chunks: list[_NormalizedStreamChunk] = []
                        if channel == _STREAM_CHANNEL_FINAL_ANSWER and part.get("type") == "text":
                            raw_key = f"raw:{message_id or 'unknown'}"
                            previous_raw = buffered_text.get(raw_key, "")
                            if isinstance(delta, str) and delta:
                                next_raw = previous_raw + delta
                            elif isinstance(part.get("text"), str):
                                next_raw = part["text"]
                            else:
                                continue
                            if next_raw == previous_raw:
                                continue
                            buffered_text[raw_key] = next_raw

                            parsed_channels = _parse_embedded_channels(next_raw)
                            for parsed_channel_name, current_parsed_text in parsed_channels.items():
                                parsed_buffer_key = (
                                    f"{parsed_channel_name}:{message_id or 'unknown'}"
                                )
                                previous_parsed_text = buffered_text.get(parsed_buffer_key, "")
                                if not current_parsed_text and not previous_parsed_text:
                                    continue
                                if current_parsed_text == previous_parsed_text:
                                    continue

                                if current_parsed_text.startswith(previous_parsed_text):
                                    chunk_text = current_parsed_text[len(previous_parsed_text) :]
                                    append = True
                                    source = "parsed_diff"
                                else:
                                    chunk_text = current_parsed_text
                                    append = False
                                    source = "parsed_reset"
                                buffered_text[parsed_buffer_key] = current_parsed_text

                                if chunk_text:
                                    chunks.append(
                                        _NormalizedStreamChunk(
                                            text=chunk_text,
                                            append=append,
                                            channel=parsed_channel_name,
                                            source=source,
                                            event_type=event_type,
                                            message_id=message_id,
                                            role=role,
                                        )
                                    )
                        else:
                            chunk_text: str | None = None
                            append = True
                            source = "delta"
                            buffer_key = f"{channel}:{message_id or 'unknown'}"
                            previous = buffered_text.get(buffer_key, "")
                            if isinstance(delta, str) and delta:
                                chunk_text = delta
                                buffered_text[buffer_key] = f"{previous}{delta}"
                            elif part.get("type") == "text" and isinstance(part.get("text"), str):
                                next_text = part["text"]
                                if next_text != previous:
                                    if next_text.startswith(previous):
                                        chunk_text = next_text[len(previous) :]
                                        append = True
                                        source = "part_text_diff"
                                    else:
                                        chunk_text = next_text
                                        append = False
                                        source = "part_text_reset"
                                    buffered_text[buffer_key] = next_text
                            if chunk_text:
                                chunks.append(
                                    _NormalizedStreamChunk(
                                        text=chunk_text,
                                        append=append,
                                        channel=channel,
                                        source=source,
                                        event_type=event_type,
                                        message_id=message_id,
                                        role=role,
                                    )
                                )

                        if not chunks:
                            continue

                        for chunk in chunks:
                            if not stream_state.matches_expected_message(chunk.message_id):
                                continue
                            if stream_state.should_drop_initial_user_echo(
                                chunk.text, channel=chunk.channel, role=chunk.role
                            ):
                                continue
                            should_emit, effective_append = stream_state.register_chunk(
                                channel=chunk.channel,
                                text=chunk.text,
                                append=chunk.append,
                            )
                            if not should_emit:
                                continue
                            await _enqueue_artifact_update(
                                event_queue=event_queue,
                                task_id=task_id,
                                context_id=context_id,
                                artifact_id=_artifact_id_for_stream_channel(
                                    artifact_id, chunk.channel
                                ),
                                text=chunk.text,
                                append=effective_append,
                                last_chunk=False,
                                artifact_metadata=_build_stream_artifact_metadata(
                                    channel=chunk.channel,
                                    source=chunk.source,
                                    event_type=chunk.event_type,
                                    message_id=chunk.message_id,
                                    role=chunk.role,
                                ),
                            )
                            logger.debug(
                                "Stream chunk task_id=%s session_id=%s "
                                "channel=%s append=%s text=%s",
                                task_id,
                                session_id,
                                chunk.channel,
                                effective_append,
                                chunk.text,
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
    artifact_metadata: Mapping[str, Any] | None = None,
    event_metadata: Mapping[str, Any] | None = None,
) -> None:
    artifact = Artifact(
        artifact_id=artifact_id,
        parts=[TextPart(text=text)],
        metadata=dict(artifact_metadata) if artifact_metadata else None,
    )
    await event_queue.enqueue_event(
        TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=append,
            last_chunk=last_chunk,
            metadata=dict(event_metadata) if event_metadata else None,
        )
    )


def _artifact_id_for_stream_channel(base_artifact_id: str, channel: str) -> str:
    if channel == _STREAM_CHANNEL_FINAL_ANSWER:
        return base_artifact_id
    return f"{base_artifact_id}:{channel}"


def _build_stream_artifact_metadata(
    *,
    channel: str,
    source: str,
    event_type: str,
    message_id: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    opencode_meta: dict[str, Any] = {
        "channel": channel,
        "source": source,
        "event_type": event_type,
    }
    if message_id:
        opencode_meta["message_id"] = message_id
    if role:
        opencode_meta["role"] = role
    return {"opencode": opencode_meta}


def _normalize_role(role: Any) -> str | None:
    if not isinstance(role, str):
        return None
    value = role.strip().lower()
    if not value:
        return None
    if value.startswith("role_"):
        value = value[5:]
    if value in {"assistant", "agent", "model", "ai"}:
        return "agent"
    if value in {"user", "human"}:
        return "user"
    if value == "system":
        return "system"
    return value


def _extract_stream_role(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    role = part.get("role") or props.get("role")
    if role is None:
        message = props.get("message")
        if isinstance(message, Mapping):
            role = message.get("role")
    return _normalize_role(role)


def _extract_stream_session_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    session_keys = ("sessionID", "sessionId", "session_id")
    for key in session_keys:
        value = part.get(key)
        if isinstance(value, str) and value:
            return value
    message = props.get("message")
    if isinstance(message, Mapping):
        for key in session_keys:
            value = message.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_stream_message_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    message_keys = ("messageID", "messageId", "message_id", "id")
    for key in message_keys:
        value = part.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    for key in message_keys:
        value = props.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    message = props.get("message")
    if isinstance(message, Mapping):
        for key in message_keys:
            value = message.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    return normalized
        info = message.get("info")
        if isinstance(info, Mapping):
            for key in message_keys:
                value = info.get(key)
                if isinstance(value, str):
                    normalized = value.strip()
                    if normalized:
                        return normalized
    return None


def _parse_embedded_channels(text: str) -> dict[str, str]:
    channels = {
        _STREAM_CHANNEL_FINAL_ANSWER: "",
        _STREAM_CHANNEL_REASONING: "",
        _STREAM_CHANNEL_TOOL_CALL: "",
    }
    i = 0

    while i < len(text):
        if text.startswith("<think>", i):
            end = text.find("</think>", i + 7)
            if end < 0:
                # Keep incomplete reasoning tag in pending tail; wait for next chunk.
                break
            channels[_STREAM_CHANNEL_REASONING] += text[i + 7 : end]
            i = end + 8
            continue

        if text.startswith("[tool_call:", i):
            j = i + 11
            depth = 1
            while j < len(text) and depth > 0:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                j += 1
            if depth > 0:
                # Keep incomplete tool call in pending tail; wait for next chunk.
                break
            channels[_STREAM_CHANNEL_TOOL_CALL] += text[i:j]
            i = j
            continue

        remaining = text[i:]
        if _is_partial_marker_prefix(remaining):
            break

        channels[_STREAM_CHANNEL_FINAL_ANSWER] += text[i]
        i += 1

    return channels


def _is_partial_marker_prefix(remaining: str) -> bool:
    if not remaining:
        return False
    if remaining != "<think>" and "<think>".startswith(remaining):
        return True
    if remaining != "[tool_call:" and "[tool_call:".startswith(remaining):
        return True
    return False


def _classify_stream_channel(part: Mapping[str, Any], props: Mapping[str, Any]) -> str:
    def _iter_candidates() -> list[str]:
        candidates: list[str] = []
        for value in (
            part.get("channel"),
            props.get("channel"),
            part.get("kind"),
            props.get("kind"),
            part.get("type"),
            props.get("type"),
            props.get("deltaType"),
            props.get("contentType"),
            props.get("phase"),
            props.get("name"),
        ):
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip().lower())
        return candidates

    candidates = _iter_candidates()
    if any(
        any(keyword in candidate for keyword in ("reason", "thinking", "thought"))
        for candidate in candidates
    ):
        return _STREAM_CHANNEL_REASONING
    if any(
        any(
            keyword in candidate
            for keyword in (
                "tool",
                "function_call",
                "functioncall",
                "tool_call",
                "toolcall",
                "action",
            )
        )
        for candidate in candidates
    ):
        return _STREAM_CHANNEL_TOOL_CALL
    return _STREAM_CHANNEL_FINAL_ANSWER


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
        meta = context.metadata
        if isinstance(meta, Mapping):
            candidate = meta.get("opencode_session_id")
    except Exception:
        candidate = None

    if not candidate and context.message is not None:
        msg_meta = getattr(context.message, "metadata", None) or {}
        if isinstance(msg_meta, Mapping):
            candidate = msg_meta.get("opencode_session_id")

    if isinstance(candidate, str):
        value = candidate.strip()
        return value or None
    return None

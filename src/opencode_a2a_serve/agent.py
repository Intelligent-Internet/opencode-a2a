from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
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

_INTERRUPT_ASKED_EVENT_TYPES = {"permission.asked", "question.asked"}
_INTERRUPT_RESOLVED_EVENT_TYPES = {"permission.replied", "question.replied", "question.rejected"}


class BlockType(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"


@dataclass(frozen=True)
class _NormalizedStreamChunk:
    text: str
    append: bool
    block_type: BlockType
    source: str
    message_id: str | None
    role: str | None


@dataclass(frozen=True)
class _PendingDelta:
    field: str
    delta: str
    message_id: str | None


@dataclass
class _StreamPartState:
    block_type: BlockType
    message_id: str | None
    role: str | None
    buffer: str = ""
    saw_delta: bool = False


@dataclass
class _StreamOutputState:
    user_text: str
    stable_message_id: str
    event_id_namespace: str
    content_buffers: dict[BlockType, str] = field(default_factory=dict)
    token_usage: dict[str, Any] | None = None
    pending_interrupt_request_ids: set[str] = field(default_factory=set)
    saw_any_chunk: bool = False
    emitted_stream_chunk: bool = False
    sequence: int = 0

    def matches_expected_message(self, message_id: str | None) -> bool:
        return bool(message_id)

    def should_drop_initial_user_echo(
        self,
        text: str,
        *,
        block_type: BlockType,
        role: str | None,
    ) -> bool:
        if role is not None:
            return False
        if block_type != BlockType.TEXT:
            return False
        if self.saw_any_chunk:
            return False
        user_text = self.user_text.strip()
        return bool(user_text) and text.strip() == user_text

    def register_chunk(
        self, *, block_type: BlockType, text: str, append: bool
    ) -> tuple[bool, bool]:
        previous = self.content_buffers.get(block_type, "")
        next_value = f"{previous}{text}" if append else text
        if next_value == previous:
            return False, False
        self.content_buffers[block_type] = next_value
        self.saw_any_chunk = True
        # Single-artifact stream must stay append-only after the first emitted chunk.
        effective_append = self.emitted_stream_chunk
        self.emitted_stream_chunk = True
        return True, effective_append

    def should_emit_final_snapshot(self, text: str) -> bool:
        if not text.strip():
            return False
        existing = self.content_buffers.get(BlockType.TEXT, "")
        if existing.strip() == text.strip():
            return False
        self.content_buffers[BlockType.TEXT] = text
        self.saw_any_chunk = True
        return True

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def resolve_message_id(self, message_id: str | None) -> str:
        if isinstance(message_id, str):
            normalized = message_id.strip()
            if normalized:
                return normalized
        return self.stable_message_id

    def build_event_id(self, sequence: int) -> str:
        return f"{self.event_id_namespace}:{sequence}"

    def ingest_token_usage(self, usage: Mapping[str, Any] | None) -> None:
        self.token_usage = _merge_token_usage(self.token_usage, usage)

    def mark_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            return False
        if normalized in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.add(normalized)
        return True

    def clear_interrupt_pending(self, request_id: str) -> None:
        self.pending_interrupt_request_ids.discard(request_id.strip())


@dataclass(frozen=True)
class _UpstreamErrorProfile:
    error_type: str
    state: TaskState
    default_message: str


_UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS: dict[int, _UpstreamErrorProfile] = {
    400: _UpstreamErrorProfile(
        "UPSTREAM_BAD_REQUEST",
        TaskState.failed,
        "OpenCode rejected the request due to invalid input",
    ),
    401: _UpstreamErrorProfile(
        "UPSTREAM_UNAUTHORIZED",
        TaskState.auth_required,
        "OpenCode rejected the request due to authentication failure",
    ),
    403: _UpstreamErrorProfile(
        "UPSTREAM_PERMISSION_DENIED",
        TaskState.failed,
        "OpenCode rejected the request due to insufficient permissions",
    ),
    404: _UpstreamErrorProfile(
        "UPSTREAM_RESOURCE_NOT_FOUND",
        TaskState.failed,
        "OpenCode rejected the request because the target resource was not found",
    ),
    429: _UpstreamErrorProfile(
        "UPSTREAM_QUOTA_EXCEEDED",
        TaskState.failed,
        "OpenCode rejected the request due to quota limits",
    ),
}


def _resolve_upstream_error_profile(status: int) -> _UpstreamErrorProfile:
    if status in _UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS:
        return _UPSTREAM_HTTP_ERROR_PROFILE_BY_STATUS[status]
    if 400 <= status < 500:
        return _UpstreamErrorProfile(
            "UPSTREAM_CLIENT_ERROR",
            TaskState.failed,
            f"OpenCode rejected the request with client error {status}",
        )
    if status >= 500:
        return _UpstreamErrorProfile(
            "UPSTREAM_SERVER_ERROR",
            TaskState.failed,
            f"OpenCode rejected the request with server error {status}",
        )
    return _UpstreamErrorProfile(
        "UPSTREAM_HTTP_ERROR",
        TaskState.failed,
        f"OpenCode rejected the request with HTTP status {status}",
    )


def _extract_upstream_error_detail(response: httpx.Response | None) -> str | None:
    if response is None:
        return None

    payload = None
    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value

    text = response.text.strip()
    if text:
        return text[:512]
    return None


def _format_upstream_error(
    exc: httpx.HTTPStatusError, *, request: str
) -> tuple[str, TaskState, str]:
    status = exc.response.status_code
    profile = _resolve_upstream_error_profile(status)
    detail = _extract_upstream_error_detail(exc.response)
    if detail:
        return (
            profile.error_type,
            profile.state,
            f"{profile.default_message} ({request}, status={status}, detail={detail}).",
        )
    return (
        profile.error_type,
        profile.state,
        f"{profile.default_message} ({request}, status={status}).",
    )


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
        cancel_abort_timeout_seconds: float = 2.0,
        session_cache_ttl_seconds: int = 3600,
        session_cache_maxsize: int = 10_000,
    ) -> None:
        self._client = client
        self._streaming_enabled = streaming_enabled
        self._cancel_abort_timeout_seconds = max(0.0, float(cancel_abort_timeout_seconds))
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
        self._running_session_ids: dict[tuple[str, str], str] = {}
        self._running_directories: dict[tuple[str, str], str] = {}

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
                state=TaskState.failed,
                streaming_request=self._should_stream(context),
            )
            return

        call_context = context.call_context
        identity = (call_context.state.get("identity") if call_context else None) or "anonymous"

        streaming_request = self._should_stream(context)
        user_text = context.get_user_input().strip()
        bound_session_id = _extract_opencode_session_id(context)

        # Directory validation
        metadata = context.metadata
        if metadata is not None and not isinstance(metadata, Mapping):
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Invalid metadata: expected an object/map.",
                state=TaskState.failed,
                streaming_request=streaming_request,
            )
            return
        requested_dir = _extract_opencode_directory(context)

        try:
            directory = self._resolve_and_validate_directory(requested_dir)
        except ValueError as e:
            logger.warning("Directory validation failed: %s", e)
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=str(e),
                state=TaskState.failed,
                streaming_request=streaming_request,
            )
            return

        if not user_text:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Only text input is supported.",
                state=TaskState.failed,
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
        stream_state = _StreamOutputState(
            user_text=user_text,
            stable_message_id=f"{task_id}:{context_id}:assistant",
            event_id_namespace=f"{task_id}:{context_id}:{stream_artifact_id}",
        )
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
            async with self._lock:
                self._running_session_ids[execution_key] = session_id
                self._running_directories[execution_key] = directory
            session_lock = await self._get_session_lock(session_id)
            await session_lock.acquire()

            if streaming_request:
                stream_task = asyncio.create_task(
                    self._consume_opencode_stream(
                        session_id=session_id,
                        identity=identity,
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
            resolved_message_id = stream_state.resolve_message_id(response.message_id)
            resolved_token_usage = _merge_token_usage(
                _extract_token_usage(response.raw),
                stream_state.token_usage,
            )
            logger.debug(
                "OpenCode response task_id=%s session_id=%s message_id=%s text=%s",
                task_id,
                response.session_id,
                resolved_message_id,
                response_text,
            )
            if streaming_request:
                if stream_state.should_emit_final_snapshot(response_text):
                    sequence = stream_state.next_sequence()
                    await _enqueue_artifact_update(
                        event_queue=event_queue,
                        task_id=task_id,
                        context_id=context_id,
                        artifact_id=stream_artifact_id,
                        text=response_text,
                        append=stream_state.emitted_stream_chunk,
                        last_chunk=True,
                        artifact_metadata=_build_stream_artifact_metadata(
                            block_type=BlockType.TEXT,
                            source="final_snapshot",
                            message_id=resolved_message_id,
                            event_id=stream_state.build_event_id(sequence),
                        ),
                    )
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(
                            state=TaskState.completed,
                        ),
                        final=True,
                        metadata={
                            "opencode": {
                                "session_id": response.session_id,
                                "message_id": resolved_message_id,
                                "event_id": f"{stream_state.event_id_namespace}:status",
                                **(
                                    {"usage": resolved_token_usage}
                                    if resolved_token_usage is not None
                                    else {}
                                ),
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
                    message_id=resolved_message_id,
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
                    status=TaskStatus(state=TaskState.completed),
                    history=history,
                    artifacts=[artifact],
                    metadata={
                        "opencode": {
                            "session_id": response.session_id,
                            **(
                                {"usage": resolved_token_usage}
                                if resolved_token_usage is not None
                                else {}
                            ),
                        }
                    },
                )
                # Attach the assistant message as the current status message.
                task.status.message = assistant_message
                await event_queue.enqueue_event(task)
        except httpx.HTTPStatusError as exc:
            logger.exception("OpenCode request failed with HTTP error")
            error_type, state, message = _format_upstream_error(
                exc,
                request="send_message",
            )
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=message,
                state=state,
                error_type=error_type,
                upstream_status=exc.response.status_code,
                streaming_request=streaming_request,
            )
        except httpx.TimeoutException as exc:
            logger.exception("OpenCode request timed out")
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=f"OpenCode request timed out: {exc}",
                state=TaskState.failed,
                error_type="UPSTREAM_TIMEOUT",
                streaming_request=streaming_request,
            )
        except Exception as exc:
            logger.exception("OpenCode request failed")
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=f"OpenCode error: {exc}",
                state=TaskState.failed,
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
                self._running_session_ids.pop(execution_key, None)
                self._running_directories.pop(execution_key, None)

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
                    state=TaskState.failed,
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
                running_session_id = self._running_session_ids.get(execution_key)
                running_directory = self._running_directories.get(execution_key)
                if running_session_id is None and running_task and not running_task.done():
                    running_session_id = self._sessions.get((running_identity, context_id))
                self._sessions.pop((running_identity, context_id))
                inflight = self._inflight_session_creates.pop((running_identity, context_id), None)
            if stop_event:
                stop_event.set()
            should_cancel_running_task = (
                running_task
                and running_task is not asyncio.current_task()
                and not running_task.done()
            )
            if running_session_id and should_cancel_running_task:
                try:
                    await asyncio.wait_for(
                        self._client.abort_session(
                            running_session_id,
                            directory=running_directory,
                        ),
                        timeout=self._cancel_abort_timeout_seconds,
                    )
                except TimeoutError:
                    logger.warning(
                        (
                            "Best-effort session abort timed out task_id=%s "
                            "context_id=%s session_id=%s timeout=%.2fs"
                        ),
                        task_id,
                        context_id,
                        running_session_id,
                        self._cancel_abort_timeout_seconds,
                    )
                except (httpx.HTTPError, RuntimeError) as exc:
                    logger.warning(
                        (
                            "Best-effort session abort failed task_id=%s "
                            "context_id=%s session_id=%s: %s"
                        ),
                        task_id,
                        context_id,
                        running_session_id,
                        exc,
                    )
            if should_cancel_running_task:
                if running_task is not None:
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
                        state=TaskState.failed,
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
        state: TaskState,
        error_type: str | None = None,
        upstream_status: int | None = None,
        streaming_request: bool,
    ) -> None:
        error_message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.agent,
            parts=[TextPart(text=message)],
            task_id=task_id,
            context_id=context_id,
        )
        error_metadata: dict[str, Any] | None = None
        if error_type or upstream_status is not None:
            error_payload: dict[str, Any] = {}
            if error_type:
                error_payload["type"] = error_type
            if upstream_status is not None:
                error_payload["upstream_status"] = upstream_status
            error_metadata = {"opencode": {"error": error_payload}}
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
                    status=TaskStatus(state=state),
                    metadata=error_metadata,
                    final=True,
                )
            )
            return
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=state, message=error_message),
            history=[error_message],
            metadata=error_metadata,
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
        identity: str,
        task_id: str,
        context_id: str,
        artifact_id: str,
        stream_state: _StreamOutputState,
        event_queue: EventQueue,
        stop_event: asyncio.Event,
        directory: str | None = None,
    ) -> None:
        part_states: dict[str, _StreamPartState] = {}
        pending_deltas: defaultdict[str, list[_PendingDelta]] = defaultdict(list)
        backoff = 0.5
        max_backoff = 5.0

        async def _emit_chunks(chunks: list[_NormalizedStreamChunk]) -> None:
            for chunk in chunks:
                if not stream_state.matches_expected_message(chunk.message_id):
                    continue
                resolved_message_id = stream_state.resolve_message_id(chunk.message_id)
                if stream_state.should_drop_initial_user_echo(
                    chunk.text,
                    block_type=chunk.block_type,
                    role=chunk.role,
                ):
                    continue
                should_emit, effective_append = stream_state.register_chunk(
                    block_type=chunk.block_type,
                    text=chunk.text,
                    append=chunk.append,
                )
                if not should_emit:
                    continue
                sequence = stream_state.next_sequence()
                await _enqueue_artifact_update(
                    event_queue=event_queue,
                    task_id=task_id,
                    context_id=context_id,
                    artifact_id=artifact_id,
                    text=chunk.text,
                    append=effective_append,
                    last_chunk=False,
                    artifact_metadata=_build_stream_artifact_metadata(
                        block_type=chunk.block_type,
                        source=chunk.source,
                        message_id=resolved_message_id,
                        role=chunk.role,
                        event_id=stream_state.build_event_id(sequence),
                    ),
                )
                logger.debug(
                    "Stream chunk task_id=%s session_id=%s block_type=%s append=%s text=%s",
                    task_id,
                    session_id,
                    chunk.block_type,
                    effective_append,
                    chunk.text,
                )

        async def _emit_interrupt_status(
            *,
            state: TaskState,
            request_id: str,
            interrupt_type: str,
            details: Mapping[str, Any],
        ) -> None:
            sequence = stream_state.next_sequence()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=state),
                    final=False,
                    metadata={
                        "opencode": {
                            "session_id": session_id,
                            "message_id": stream_state.resolve_message_id(None),
                            "event_id": stream_state.build_event_id(sequence),
                            "interrupt": {
                                "request_id": request_id,
                                "type": interrupt_type,
                                "details": dict(details),
                            },
                        }
                    },
                )
            )

        def _new_chunk(
            *,
            text: str,
            append: bool,
            block_type: BlockType,
            source: str,
            message_id: str | None,
            role: str | None,
        ) -> _NormalizedStreamChunk:
            return _NormalizedStreamChunk(
                text=text,
                append=append,
                block_type=block_type,
                source=source,
                message_id=message_id,
                role=role,
            )

        def _upsert_part_state(
            *,
            part_id: str,
            part: Mapping[str, Any],
            props: Mapping[str, Any],
            role: str | None,
            message_id: str | None,
        ) -> _StreamPartState | None:
            block_type = _resolve_stream_block_type(part, props)
            if block_type is None:
                return None
            state = part_states.get(part_id)
            if state is None:
                state = _StreamPartState(
                    block_type=block_type,
                    message_id=message_id,
                    role=role,
                )
                part_states[part_id] = state
                return state
            state.block_type = block_type
            if role is not None:
                state.role = role
            if message_id:
                state.message_id = message_id
            return state

        def _delta_chunks(
            *,
            state: _StreamPartState,
            delta_text: str,
            message_id: str | None,
            source: str,
        ) -> list[_NormalizedStreamChunk]:
            if not delta_text:
                return []
            if message_id:
                state.message_id = message_id
            state.buffer = f"{state.buffer}{delta_text}"
            state.saw_delta = True
            return [
                _new_chunk(
                    text=delta_text,
                    append=True,
                    block_type=state.block_type,
                    source=source,
                    message_id=state.message_id,
                    role=state.role,
                )
            ]

        def _snapshot_chunks(
            *,
            state: _StreamPartState,
            snapshot: str,
            message_id: str | None,
            part_id: str,
        ) -> list[_NormalizedStreamChunk]:
            if message_id:
                state.message_id = message_id
            previous = state.buffer
            if snapshot == previous:
                return []
            if snapshot.startswith(previous):
                delta_text = snapshot[len(previous) :]
                state.buffer = snapshot
                if not delta_text:
                    return []
                return [
                    _new_chunk(
                        text=delta_text,
                        append=True,
                        block_type=state.block_type,
                        source="part_text_diff",
                        message_id=state.message_id,
                        role=state.role,
                    )
                ]
            state.buffer = snapshot
            logger.warning(
                "Suppressing non-prefix snapshot rewrite "
                "task_id=%s session_id=%s part_id=%s block_type=%s had_delta=%s",
                task_id,
                session_id,
                part_id,
                state.block_type.value,
                state.saw_delta,
            )
            return []

        def _tool_chunks(
            *,
            state: _StreamPartState,
            part: Mapping[str, Any],
            message_id: str | None,
        ) -> list[_NormalizedStreamChunk]:
            tool_chunk = _serialize_tool_part(part)
            if not tool_chunk:
                return []
            if message_id:
                state.message_id = message_id
            previous = state.buffer
            if tool_chunk == previous:
                return []
            state.buffer = tool_chunk
            text = tool_chunk if not previous else f"\n{tool_chunk}"
            return [
                _new_chunk(
                    text=text,
                    append=bool(previous),
                    block_type=state.block_type,
                    source="tool_part_update",
                    message_id=state.message_id,
                    role=state.role,
                )
            ]

        try:
            while not stop_event.is_set():
                try:
                    async for event in self._client.stream_events(
                        stop_event=stop_event, directory=directory
                    ):
                        if stop_event.is_set():
                            break
                        event_type = event.get("type")
                        if not isinstance(event_type, str):
                            continue
                        props = event.get("properties")
                        if not isinstance(props, Mapping):
                            continue
                        event_session_id = _extract_event_session_id(event)
                        if event_session_id == session_id:
                            usage = _extract_token_usage(event)
                            if usage is not None:
                                stream_state.ingest_token_usage(usage)
                            asked = _extract_interrupt_asked_event(event)
                            if asked is not None:
                                request_id = asked["request_id"]
                                if stream_state.mark_interrupt_pending(request_id):
                                    remember_request = getattr(
                                        self._client, "remember_interrupt_request", None
                                    )
                                    if callable(remember_request):
                                        remember_request(
                                            request_id=request_id,
                                            session_id=session_id,
                                            interrupt_type=asked["interrupt_type"],
                                            identity=identity,
                                            task_id=task_id,
                                            context_id=context_id,
                                        )
                                    await _emit_interrupt_status(
                                        state=TaskState.input_required,
                                        request_id=request_id,
                                        interrupt_type=asked["interrupt_type"],
                                        details=asked["details"],
                                    )
                            resolved = _extract_interrupt_resolved_event(event)
                            if resolved is not None:
                                resolved_request_id = resolved["request_id"]
                                stream_state.clear_interrupt_pending(resolved_request_id)
                                discard_request = getattr(
                                    self._client, "discard_interrupt_request", None
                                )
                                if callable(discard_request):
                                    discard_request(resolved_request_id)
                        if event_type not in {"message.part.updated", "message.part.delta"}:
                            continue
                        part = props.get("part")
                        if not isinstance(part, Mapping):
                            part = {}
                        if _extract_stream_session_id(part, props) != session_id:
                            continue
                        message_id = _extract_stream_message_id(part, props)
                        part_id = _extract_stream_part_id(part, props)
                        if not part_id:
                            continue

                        if event_type == "message.part.delta":
                            field = props.get("field")
                            delta = props.get("delta")
                            if field != "text" or not isinstance(delta, str) or not delta:
                                continue
                            state = part_states.get(part_id)
                            if state is None:
                                pending_deltas[part_id].append(
                                    _PendingDelta(
                                        field=field,
                                        delta=delta,
                                        message_id=message_id,
                                    )
                                )
                                continue
                            if state.role in {"user", "system"}:
                                continue
                            chunks = _delta_chunks(
                                state=state,
                                delta_text=delta,
                                message_id=message_id,
                                source="delta_event",
                            )
                            if chunks:
                                await _emit_chunks(chunks)
                            continue

                        role = _extract_stream_role(part, props)
                        state = _upsert_part_state(
                            part_id=part_id,
                            part=part,
                            props=props,
                            role=role,
                            message_id=message_id,
                        )
                        if state is None:
                            pending_deltas.pop(part_id, None)
                            continue
                        if state.role in {"user", "system"}:
                            pending_deltas.pop(part_id, None)
                            continue

                        chunks: list[_NormalizedStreamChunk] = []
                        pending = pending_deltas.pop(part_id, [])
                        for buffered in pending:
                            if buffered.field != "text":
                                continue
                            chunks.extend(
                                _delta_chunks(
                                    state=state,
                                    delta_text=buffered.delta,
                                    message_id=buffered.message_id,
                                    source="delta_event_buffered",
                                )
                            )

                        delta = props.get("delta")
                        if isinstance(delta, str) and delta:
                            chunks.extend(
                                _delta_chunks(
                                    state=state,
                                    delta_text=delta,
                                    message_id=message_id,
                                    source="delta",
                                )
                            )
                        elif state.block_type == BlockType.TOOL_CALL:
                            chunks.extend(
                                _tool_chunks(
                                    state=state,
                                    part=part,
                                    message_id=message_id,
                                )
                            )
                        elif isinstance(part.get("text"), str):
                            chunks.extend(
                                _snapshot_chunks(
                                    state=state,
                                    snapshot=part["text"],
                                    message_id=message_id,
                                    part_id=part_id,
                                )
                            )

                        if chunks:
                            await _emit_chunks(chunks)

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
    normalized_last_chunk = True if last_chunk is True else None
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
            last_chunk=normalized_last_chunk,
            metadata=dict(event_metadata) if event_metadata else None,
        )
    )


def _build_stream_artifact_metadata(
    *,
    block_type: BlockType,
    source: str,
    message_id: str | None = None,
    role: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    opencode_meta: dict[str, Any] = {
        "block_type": block_type,
        "source": source,
    }
    if message_id:
        opencode_meta["message_id"] = message_id
    if role:
        opencode_meta["role"] = role
    if event_id:
        opencode_meta["event_id"] = event_id
    return {"opencode": opencode_meta}


def _coerce_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        if "." in normalized or "e" in normalized.lower():
            parsed = float(normalized)
            if parsed.is_integer():
                return int(parsed)
            return parsed
        return int(normalized)
    except ValueError:
        return None


def _extract_usage_from_info_like(info: Mapping[str, Any]) -> dict[str, Any] | None:
    tokens = info.get("tokens")
    if not isinstance(tokens, Mapping):
        return None

    usage: dict[str, Any] = {}

    input_tokens = _coerce_number(tokens.get("input"))
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens

    output_tokens = _coerce_number(tokens.get("output"))
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens

    total_tokens = _coerce_number(tokens.get("total"))
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    elif input_tokens is not None and output_tokens is not None:
        usage["total_tokens"] = input_tokens + output_tokens

    reasoning_tokens = _coerce_number(tokens.get("reasoning"))
    if reasoning_tokens is not None:
        usage["reasoning_tokens"] = reasoning_tokens

    cache = tokens.get("cache")
    if isinstance(cache, Mapping):
        cache_usage: dict[str, Any] = {}
        cache_read = _coerce_number(cache.get("read"))
        if cache_read is not None:
            cache_usage["read_tokens"] = cache_read
        cache_write = _coerce_number(cache.get("write"))
        if cache_write is not None:
            cache_usage["write_tokens"] = cache_write
        if cache_usage:
            usage["cache_tokens"] = cache_usage

    cost = _coerce_number(info.get("cost"))
    if cost is not None:
        usage["cost"] = cost

    if not usage:
        return None
    return usage


def _extract_token_usage(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = []
    info = payload.get("info")
    if isinstance(info, Mapping):
        candidates.append(info)

    props = payload.get("properties")
    if isinstance(props, Mapping):
        props_info = props.get("info")
        if isinstance(props_info, Mapping):
            candidates.append(props_info)
        part = props.get("part")
        if isinstance(part, Mapping):
            candidates.append(part)

    for candidate in candidates:
        usage = _extract_usage_from_info_like(candidate)
        if usage is not None:
            return usage
    return None


def _merge_token_usage(
    base: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if base is None and incoming is None:
        return None
    merged: dict[str, Any] = dict(base) if base else {}
    if incoming:
        for key, value in incoming.items():
            if value is None:
                continue
            if key == "raw" and isinstance(value, Mapping):
                existing = merged.get("raw")
                if isinstance(existing, Mapping):
                    merged["raw"] = {**dict(existing), **dict(value)}
                else:
                    merged["raw"] = dict(value)
                continue
            merged[key] = value
    return merged or None


def _normalize_role(role: Any) -> str | None:
    if not isinstance(role, str):
        return None
    value = role.strip().lower()
    if not value:
        return None
    if value == "assistant":
        return "agent"
    if value == "user":
        return "user"
    if value == "system":
        return "system"
    return None


def _extract_stream_role(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    role = part.get("role") or props.get("role")
    return _normalize_role(role)


def _extract_first_nonempty_string(
    source: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> str | None:
    if not isinstance(source, Mapping):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _extract_stream_session_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = _extract_first_nonempty_string(part, ("sessionID",))
    if candidate:
        return candidate
    return _extract_first_nonempty_string(props, ("sessionID",))


def _extract_event_session_id(event: Mapping[str, Any]) -> str | None:
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    direct = _extract_first_nonempty_string(props, ("sessionID",))
    if direct:
        return direct
    info = props.get("info")
    if isinstance(info, Mapping):
        info_session_id = _extract_first_nonempty_string(info, ("sessionID",))
        if info_session_id:
            return info_session_id
    part = props.get("part")
    if isinstance(part, Mapping):
        part_session_id = _extract_first_nonempty_string(part, ("sessionID",))
        if part_session_id:
            return part_session_id
    return None


def _extract_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return result


def _extract_interrupt_asked_request_id(props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string(
        props,
        ("id",),
    )


def _extract_interrupt_resolved_request_id(props: Mapping[str, Any]) -> str | None:
    return _extract_first_nonempty_string(
        props,
        ("requestID",),
    )


def _extract_interrupt_asked_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_ASKED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = _extract_interrupt_asked_request_id(props)
    if not request_id:
        return None
    if event_type == "permission.asked":
        details: dict[str, Any] = {
            "permission": props.get("permission"),
            "patterns": _extract_string_list(props.get("patterns")),
        }
        return {
            "request_id": request_id,
            "interrupt_type": "permission",
            "details": details,
        }
    questions = props.get("questions")
    details = {"questions": questions if isinstance(questions, list) else []}
    return {
        "request_id": request_id,
        "interrupt_type": "question",
        "details": details,
    }


def _extract_interrupt_resolved_event(event: Mapping[str, Any]) -> dict[str, str] | None:
    event_type = event.get("type")
    if event_type not in _INTERRUPT_RESOLVED_EVENT_TYPES:
        return None
    props = event.get("properties")
    if not isinstance(props, Mapping):
        return None
    request_id = _extract_interrupt_resolved_request_id(props)
    if not request_id:
        return None
    return {"request_id": request_id, "event_type": event_type}


def _extract_stream_message_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = _extract_first_nonempty_string(part, ("messageID",))
    if candidate:
        return candidate
    return _extract_first_nonempty_string(props, ("messageID",))


def _extract_stream_part_id(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    candidate = _extract_first_nonempty_string(part, ("id",))
    if candidate:
        return candidate
    return _extract_first_nonempty_string(props, ("partID",))


def _extract_stream_part_type(part: Mapping[str, Any], props: Mapping[str, Any]) -> str | None:
    del props
    value = part.get("type")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized:
            return normalized
    return None


def _map_part_type_to_block_type(part_type: str | None) -> BlockType | None:
    if not part_type:
        return None
    if part_type == "text":
        return BlockType.TEXT
    if part_type == "reasoning":
        return BlockType.REASONING
    if part_type == "tool":
        return BlockType.TOOL_CALL
    return None


def _resolve_stream_block_type(
    part: Mapping[str, Any], props: Mapping[str, Any]
) -> BlockType | None:
    return _map_part_type_to_block_type(_extract_stream_part_type(part, props))


def _serialize_tool_part(part: Mapping[str, Any]) -> str | None:
    payload: dict[str, Any] = {}
    for source_key in ("callID",):
        value = part.get(source_key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                payload["call_id"] = normalized
                break
    for source_key in ("tool",):
        value = part.get(source_key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                payload["tool"] = normalized
                break
    state = part.get("state")
    if isinstance(state, Mapping):
        status = state.get("status")
        if isinstance(status, str):
            normalized = status.strip()
            if normalized:
                payload["status"] = normalized
        for key in ("title", "subtitle", "input", "output", "error"):
            value = state.get(key)
            if value is not None:
                payload[key] = value
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_history(context: RequestContext) -> list[Message]:
    if context.current_task and context.current_task.history:
        history = list(context.current_task.history)
    else:
        history = []
        if context.message:
            history.append(context.message)
    # Do not append assistant message to history; it lives in status.message.
    return history


def _iter_opencode_metadata_maps(context: RequestContext):
    try:
        meta = context.metadata
    except Exception:
        meta = None

    if isinstance(meta, Mapping):
        opencode_meta = meta.get("opencode")
        if isinstance(opencode_meta, Mapping):
            yield opencode_meta

    if context.message is not None:
        msg_meta = getattr(context.message, "metadata", None) or {}
        if isinstance(msg_meta, Mapping):
            opencode_meta = msg_meta.get("opencode")
            if isinstance(opencode_meta, Mapping):
                yield opencode_meta


def _extract_opencode_string_metadata(context: RequestContext, field: str) -> str | None:
    for opencode_meta in _iter_opencode_metadata_maps(context):
        candidate = opencode_meta.get(field)
        if isinstance(candidate, str):
            value = candidate.strip()
            if value:
                return value
    return None


def _extract_opencode_session_id(context: RequestContext) -> str | None:
    # Contract: clients pass binding metadata under metadata.opencode.session_id.
    return _extract_opencode_string_metadata(context, "session_id")


def _extract_opencode_directory(context: RequestContext) -> str | None:
    # Contract: clients pass directory override under metadata.opencode.directory.
    return _extract_opencode_string_metadata(context, "directory")

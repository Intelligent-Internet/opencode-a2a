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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    DataPart,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from ..opencode_client import OpencodeClient, UpstreamContractError
from ..parts.mapping import (
    UnsupportedA2AInputError,
    extract_text_from_a2a_parts,
    map_a2a_parts_to_opencode_parts,
    summarize_a2a_parts,
)
from .request_context import (
    _build_history,
    _extract_opencode_directory,
    _extract_shared_model,
    _extract_shared_session_id,
)
from .stream_events import (
    BlockType,
    _build_progress_identity,
    _coerce_number,
    _extract_event_session_id,
    _extract_interrupt_asked_event,
    _extract_interrupt_resolved_event,
    _extract_progress_metadata,
    _extract_stream_message_id,
    _extract_stream_part_id,
    _extract_stream_role,
    _extract_stream_session_id,
    _extract_stream_snapshot_text,
    _extract_stream_terminal_signal,
    _extract_token_usage,
    _extract_tool_part_payload,
    _extract_upstream_error_from_event,
    _extract_upstream_error_from_response,
    _log_stream_event_debug,
    _normalize_interrupt_question_options,
    _normalize_interrupt_questions,
    _normalize_role,
    _preview_log_value,
    _resolve_stream_block_type,
)
from .stream_state import (
    _build_output_metadata,
    _build_stream_artifact_metadata,
    _merge_token_usage,
    _NormalizedStreamChunk,
    _PendingDelta,
    _StreamOutputState,
    _StreamPartState,
    _TTLCache,
)
from .upstream_errors import (
    _await_stream_terminal_signal,
    _extract_upstream_error_detail,
    _format_inband_upstream_error,
    _format_stream_terminal_error,
    _format_upstream_error,
    _resolve_upstream_error_profile,
    _StreamTerminalSignal,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_build_output_metadata",
    "_build_progress_identity",
    "_coerce_number",
    "_extract_event_session_id",
    "_extract_interrupt_asked_event",
    "_extract_interrupt_resolved_event",
    "_extract_progress_metadata",
    "_extract_stream_session_id",
    "_extract_stream_snapshot_text",
    "_extract_stream_terminal_signal",
    "_extract_token_usage",
    "_extract_upstream_error_detail",
    "_extract_upstream_error_from_event",
    "_extract_upstream_error_from_response",
    "_format_inband_upstream_error",
    "_format_stream_terminal_error",
    "_format_upstream_error",
    "_merge_token_usage",
    "_normalize_interrupt_question_options",
    "_normalize_interrupt_questions",
    "_normalize_role",
    "_preview_log_value",
    "_resolve_upstream_error_profile",
]


def _emit_metric(
    name: str,
    value: float = 1.0,
    **labels: str | int | float | bool,
) -> None:
    if labels:
        labels_text = ",".join(
            f"{key}={str(label).lower() if isinstance(label, bool) else label}"
            for key, label in sorted(labels.items())
        )
        logger.debug("metric=%s value=%s labels=%s", name, value, labels_text)
        return
    logger.debug("metric=%s value=%s", name, value)


@dataclass(frozen=True)
class _PreparedExecution:
    identity: str
    streaming_request: bool
    request_parts: list[Any]
    user_text: str
    session_title: str
    use_structured_parts: bool
    bound_session_id: str | None
    model_override: dict[str, str] | None
    directory: str | None


class _ExecutionCoordinator:
    def __init__(
        self,
        executor: OpencodeAgentExecutor,
        *,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        prepared: _PreparedExecution,
    ) -> None:
        self._executor = executor
        self._context = context
        self._event_queue = event_queue
        self._task_id = task_id
        self._context_id = context_id
        self._prepared = prepared
        self._stream_artifact_id = f"{task_id}:stream"
        self._stream_state = _StreamOutputState(
            user_text=prepared.user_text,
            stable_message_id=f"{task_id}:{context_id}:assistant",
            event_id_namespace=f"{task_id}:{context_id}:{self._stream_artifact_id}",
        )
        self._stream_terminal_signal: asyncio.Future[_StreamTerminalSignal] | None = None
        self._stop_event = asyncio.Event()
        self._stream_task: asyncio.Task[None] | None = None
        self._pending_preferred_claim = False
        self._session_lock: asyncio.Lock | None = None
        self._session_id = ""
        self._execution_key = (task_id, context_id)

    async def run(self) -> None:
        current_task = asyncio.current_task()
        if current_task is not None:
            await self._register_running_request(current_task)

        try:
            await self._bind_session()
            await self._enqueue_working_status()
            response = await self._send_message()
            if self._pending_preferred_claim:
                await self._executor._finalize_preferred_session_binding(
                    identity=self._prepared.identity,
                    context_id=self._context_id,
                    session_id=self._session_id,
                )
                self._pending_preferred_claim = False
            await self._handle_response(response)
        except httpx.HTTPStatusError as exc:
            logger.exception("OpenCode request failed with HTTP error")
            error_type, state, message = _format_upstream_error(
                exc,
                request="send_message",
            )
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=message,
                state=state,
                error_type=error_type,
                upstream_status=exc.response.status_code,
                streaming_request=self._prepared.streaming_request,
            )
        except httpx.TimeoutException as exc:
            logger.exception("OpenCode request timed out")
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=f"OpenCode request timed out: {exc}",
                state=TaskState.failed,
                error_type="UPSTREAM_TIMEOUT",
                streaming_request=self._prepared.streaming_request,
            )
        except UpstreamContractError as exc:
            logger.warning("OpenCode request failed with payload mismatch: %s", exc)
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=f"OpenCode payload mismatch: {exc}",
                state=TaskState.failed,
                error_type="UPSTREAM_PAYLOAD_ERROR",
                streaming_request=self._prepared.streaming_request,
            )
        except Exception as exc:
            logger.exception("OpenCode request failed")
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=f"OpenCode error: {exc}",
                state=TaskState.failed,
                streaming_request=self._prepared.streaming_request,
            )
        finally:
            await self._cleanup()

    async def _register_running_request(self, current_task: asyncio.Task[Any]) -> None:
        async with self._executor._lock:
            self._executor._running_requests[self._execution_key] = current_task
            self._executor._running_stop_events[self._execution_key] = self._stop_event
            self._executor._running_identities[self._execution_key] = self._prepared.identity

    async def _bind_session(self) -> None:
        (
            self._session_id,
            self._pending_preferred_claim,
        ) = await self._executor._get_or_create_session(
            self._prepared.identity,
            self._context_id,
            self._prepared.session_title or self._prepared.user_text,
            preferred_session_id=self._prepared.bound_session_id,
            directory=self._prepared.directory,
        )
        self._session_lock = await self._executor._get_session_lock(self._session_id)
        await self._session_lock.acquire()
        async with self._executor._lock:
            self._executor._running_session_ids[self._execution_key] = self._session_id
            self._executor._running_directories[self._execution_key] = self._prepared.directory

        if self._prepared.streaming_request:
            self._stream_terminal_signal = asyncio.get_running_loop().create_future()
            self._stream_task = asyncio.create_task(
                self._executor._consume_opencode_stream(
                    session_id=self._session_id,
                    identity=self._prepared.identity,
                    task_id=self._task_id,
                    context_id=self._context_id,
                    artifact_id=self._stream_artifact_id,
                    stream_state=self._stream_state,
                    event_queue=self._event_queue,
                    stop_event=self._stop_event,
                    directory=self._prepared.directory,
                    terminal_signal=self._stream_terminal_signal,
                )
            )

    async def _enqueue_working_status(self) -> None:
        await self._event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                status=TaskStatus(state=TaskState.working),
                final=False,
            )
        )

    async def _send_message(self) -> Any:
        send_kwargs: dict[str, Any] = {
            "directory": self._prepared.directory,
            "model_override": self._prepared.model_override,
        }
        if self._prepared.streaming_request:
            send_kwargs["timeout_override"] = self._executor._client.stream_timeout

        if not self._prepared.use_structured_parts:
            return await self._executor._client.send_message(
                self._session_id,
                self._prepared.user_text,
                **send_kwargs,
            )

        return await self._executor._client.send_message(
            self._session_id,
            self._prepared.user_text or None,
            parts=self._prepared.request_parts,
            **send_kwargs,
        )

    async def _handle_response(self, response: Any) -> None:
        response_text = response.text or ""
        resolved_message_id = self._stream_state.resolve_message_id(response.message_id)
        response_error = _extract_upstream_error_from_response(response.raw)
        resolved_token_usage = _merge_token_usage(
            _extract_token_usage(response.raw),
            self._stream_state.token_usage,
        )

        logger.debug(
            "OpenCode response task_id=%s session_id=%s message_id=%s text=%s",
            self._task_id,
            response.session_id,
            resolved_message_id,
            response_text,
        )

        if response_error is not None:
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=response_error.message,
                state=response_error.state,
                error_type=response_error.error_type,
                upstream_status=response_error.upstream_status,
                streaming_request=self._prepared.streaming_request,
            )
            return

        if self._prepared.streaming_request:
            await self._handle_streaming_response(
                response=response,
                response_text=response_text,
                resolved_message_id=resolved_message_id,
                resolved_token_usage=resolved_token_usage,
            )
            return

        await self._handle_non_streaming_response(
            response=response,
            response_text=response_text,
            resolved_message_id=resolved_message_id,
            resolved_token_usage=resolved_token_usage,
        )

    async def _handle_streaming_response(
        self,
        *,
        response: Any,
        response_text: str,
        resolved_message_id: str,
        resolved_token_usage: Mapping[str, Any] | None,
    ) -> None:
        del response
        if self._stream_terminal_signal is None:
            raise RuntimeError("Streaming terminal signal was not initialized")

        terminal_signal = await _await_stream_terminal_signal(
            stream_task=self._stream_task,
            terminal_signal=self._stream_terminal_signal,
            session_id=self._session_id,
        )
        if terminal_signal.state != TaskState.completed:
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=terminal_signal.message or "OpenCode execution failed.",
                state=terminal_signal.state,
                error_type=terminal_signal.error_type,
                upstream_status=terminal_signal.upstream_status,
                streaming_request=True,
            )
            return

        if self._stream_state.upstream_error is not None:
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=self._stream_state.upstream_error.message,
                state=self._stream_state.upstream_error.state,
                error_type=self._stream_state.upstream_error.error_type,
                upstream_status=self._stream_state.upstream_error.upstream_status,
                streaming_request=True,
            )
            return

        if self._stream_state.should_emit_final_snapshot(response_text):
            sequence = self._stream_state.next_sequence()
            await _enqueue_artifact_update(
                event_queue=self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                artifact_id=self._stream_artifact_id,
                part=Part(root=TextPart(text=response_text)),
                append=self._stream_state.emitted_stream_chunk,
                last_chunk=True,
                artifact_metadata=_build_stream_artifact_metadata(
                    block_type=BlockType.TEXT,
                    shared_source="final_snapshot",
                    message_id=resolved_message_id,
                    event_id=self._stream_state.build_event_id(sequence),
                    sequence=sequence,
                ),
            )

        await self._event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                status=TaskStatus(state=TaskState.completed),
                final=True,
                metadata=_build_output_metadata(
                    session_id=self._session_id,
                    usage=resolved_token_usage,
                    stream={
                        "message_id": resolved_message_id,
                        "event_id": f"{self._stream_state.event_id_namespace}:status",
                        "source": "status",
                    },
                ),
            )
        )

    async def _handle_non_streaming_response(
        self,
        *,
        response: Any,
        response_text: str,
        resolved_message_id: str,
        resolved_token_usage: Mapping[str, Any] | None,
    ) -> None:
        response_text = response_text or "(No text content returned by OpenCode.)"
        assistant_message = _build_assistant_message(
            task_id=self._task_id,
            context_id=self._context_id,
            text=response_text,
            message_id=resolved_message_id,
        )
        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            name="response",
            parts=[Part(root=TextPart(text=response_text))],
        )
        history = _build_history(self._context)
        task = Task(
            id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.completed),
            history=history,
            artifacts=[artifact],
            metadata=_build_output_metadata(
                session_id=response.session_id,
                usage=resolved_token_usage,
            ),
        )
        task.status.message = assistant_message
        await self._event_queue.enqueue_event(task)

    async def _cleanup(self) -> None:
        if self._pending_preferred_claim and self._session_id:
            with suppress(Exception):
                await self._executor._release_preferred_session_claim(
                    identity=self._prepared.identity,
                    session_id=self._session_id,
                )
        self._stop_event.set()
        if self._stream_task:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
        if self._session_lock and self._session_lock.locked():
            self._session_lock.release()
        async with self._executor._lock:
            self._executor._running_requests.pop(self._execution_key, None)
            self._executor._running_stop_events.pop(self._execution_key, None)
            self._executor._running_identities.pop(self._execution_key, None)
            self._executor._running_session_ids.pop(self._execution_key, None)
            self._executor._running_directories.pop(self._execution_key, None)


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
        self._running_directories: dict[tuple[str, str], str | None] = {}

    @staticmethod
    def _emit_metric(
        name: str,
        value: float = 1.0,
        **labels: str | int | float | bool,
    ) -> None:
        _emit_metric(name, value, **labels)

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

    def resolve_directory_for_control(self, requested: str | None) -> str | None:
        """Shared directory policy for session control JSON-RPC methods."""
        return self._resolve_and_validate_directory(requested)

    async def claim_session_for_control(self, *, identity: str, session_id: str) -> bool:
        """Reserve control access for a session.

        Returns True when caller created a pending ownership claim that must be finalized or
        released after upstream call completes.
        """
        return await self._claim_preferred_session(identity=identity, session_id=session_id)

    async def finalize_session_for_control(self, *, identity: str, session_id: str) -> None:
        """Finalize control-session ownership after upstream call succeeds."""
        await self._finalize_session_claim(identity=identity, session_id=session_id)

    async def release_session_for_control(self, *, identity: str, session_id: str) -> None:
        """Release pending control-session ownership on failure."""
        await self._release_preferred_session_claim(identity=identity, session_id=session_id)

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
        message_parts = (
            getattr(context.message, "parts", None) if context.message is not None else None
        )
        try:
            request_parts = map_a2a_parts_to_opencode_parts(message_parts)
        except UnsupportedA2AInputError as exc:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=str(exc),
                state=TaskState.failed,
                streaming_request=streaming_request,
            )
            return

        user_text = extract_text_from_a2a_parts(message_parts) or context.get_user_input().strip()
        session_title = user_text or summarize_a2a_parts(message_parts)
        text_only_request = (
            len(request_parts) == 1
            and request_parts[0].get("type") == "text"
            and request_parts[0].get("text") == user_text
        )
        use_structured_parts = bool(request_parts) and not text_only_request
        bound_session_id = _extract_shared_session_id(context)
        model_override = _extract_shared_model(context)
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

        if not user_text and not request_parts:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Only text and file input are supported.",
                state=TaskState.failed,
                streaming_request=streaming_request,
            )
            return

        logger.debug(
            (
                "Received message identity=%s task_id=%s context_id=%s "
                "streaming=%s text=%s part_count=%s"
            ),
            identity,
            task_id,
            context_id,
            streaming_request,
            user_text,
            len(request_parts),
        )
        prepared = _PreparedExecution(
            identity=identity,
            streaming_request=streaming_request,
            request_parts=request_parts,
            user_text=user_text,
            session_title=session_title or user_text,
            use_structured_parts=use_structured_parts,
            bound_session_id=bound_session_id,
            model_override=model_override,
            directory=directory,
        )
        coordinator = _ExecutionCoordinator(
            self,
            context=context,
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
            prepared=prepared,
        )
        await coordinator.run()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        started_at = time.monotonic()
        abort_outcome = "not_attempted"
        self._emit_metric("a2a_cancel_requests_total")
        try:
            if not task_id or not context_id:
                abort_outcome = "invalid_request_context"
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
                self._emit_metric("a2a_cancel_abort_attempt_total")
                try:
                    await asyncio.wait_for(
                        self._client.abort_session(
                            running_session_id,
                            directory=running_directory,
                        ),
                        timeout=self._cancel_abort_timeout_seconds,
                    )
                    abort_outcome = "success"
                    self._emit_metric("a2a_cancel_abort_success_total")
                except TimeoutError:
                    abort_outcome = "timeout"
                    self._emit_metric("a2a_cancel_abort_timeout_total")
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
                    abort_outcome = "error"
                    self._emit_metric("a2a_cancel_abort_error_total")
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
            elif should_cancel_running_task:
                abort_outcome = "no_session_binding"
            else:
                abort_outcome = "no_running_task"
            if should_cancel_running_task:
                if running_task is not None:
                    running_task.cancel()
            if inflight:
                inflight.cancel()
                with suppress(asyncio.CancelledError):
                    await inflight
        except Exception as exc:
            abort_outcome = "cancel_error"
            self._emit_metric("a2a_cancel_errors_total")
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
        finally:
            self._emit_metric(
                "a2a_cancel_duration_ms",
                (time.monotonic() - started_at) * 1000.0,
                abort_outcome=abort_outcome,
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
            pending_claim = await self._claim_preferred_session(
                identity=identity,
                session_id=preferred_session_id,
            )
            if not pending_claim:
                self._sessions.set((identity, context_id), preferred_session_id)
            return preferred_session_id, pending_claim

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
        await self._finalize_session_claim(identity=identity, session_id=session_id)
        async with self._lock:
            self._sessions.set((identity, context_id), session_id)

    async def _claim_preferred_session(self, *, identity: str, session_id: str) -> bool:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            pending_owner = self._pending_session_claims.get(session_id)
            if owner and owner != identity:
                logger.warning(
                    "Identity %s tried to hijack session %s owned by %s",
                    identity,
                    session_id,
                    owner,
                )
                raise PermissionError(f"Session {session_id} is not owned by you")

            if pending_owner and pending_owner != identity:
                logger.warning(
                    "Identity %s tried to use session %s while pending owner is %s",
                    identity,
                    session_id,
                    pending_owner,
                )
                raise PermissionError(f"Session {session_id} is not owned by you")

            if owner == identity:
                return False

            self._pending_session_claims[session_id] = identity
            return True

    async def _finalize_session_claim(self, *, identity: str, session_id: str) -> None:
        async with self._lock:
            owner = self._session_owners.get(session_id)
            pending_owner = self._pending_session_claims.get(session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")

            self._session_owners.set(session_id, identity)
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
            parts=[Part(root=TextPart(text=message))],
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
                part=Part(root=TextPart(text=message)),
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
        terminal_signal: asyncio.Future[_StreamTerminalSignal],
        directory: str | None = None,
    ) -> None:
        part_states: dict[str, _StreamPartState] = {}
        pending_deltas: defaultdict[str, list[_PendingDelta]] = defaultdict(list)
        backoff = 0.5
        max_backoff = 5.0

        async def _emit_chunks(chunks: list[_NormalizedStreamChunk]) -> None:
            for chunk in chunks:
                resolved_message_id = stream_state.resolve_message_id(chunk.message_id)
                chunk_text = getattr(chunk.part.root, "text", "")
                if stream_state.should_drop_initial_user_echo(
                    chunk_text,
                    block_type=chunk.block_type,
                    role=chunk.role,
                ):
                    continue
                should_emit, effective_append = stream_state.register_chunk(
                    block_type=chunk.block_type,
                    content_key=chunk.content_key,
                    append=chunk.append,
                    accumulate_content=chunk.accumulate_content,
                )
                if not should_emit:
                    continue
                sequence = stream_state.next_sequence()
                await _enqueue_artifact_update(
                    event_queue=event_queue,
                    task_id=task_id,
                    context_id=context_id,
                    artifact_id=artifact_id,
                    part=chunk.part,
                    append=effective_append,
                    last_chunk=False,
                    artifact_metadata=_build_stream_artifact_metadata(
                        block_type=chunk.block_type,
                        shared_source=chunk.shared_source,
                        message_id=resolved_message_id,
                        role=chunk.role,
                        event_id=stream_state.build_event_id(sequence),
                        sequence=sequence,
                    ),
                )
                logger.debug(
                    "Stream chunk task_id=%s session_id=%s block_type=%s append=%s "
                    "shared_source=%s internal_source=%s text=%s",
                    task_id,
                    session_id,
                    chunk.block_type,
                    effective_append,
                    chunk.shared_source,
                    chunk.internal_source,
                    chunk.content_key,
                )
                if chunk.block_type == BlockType.TOOL_CALL:
                    _emit_metric("tool_call_chunks_emitted_total")

        async def _emit_interrupt_status(
            *,
            state: TaskState,
            request_id: str,
            interrupt_type: str,
            phase: str,
            details: Mapping[str, Any] | None = None,
            resolution: str | None = None,
        ) -> None:
            interrupt_metadata: dict[str, Any] = {
                "request_id": request_id,
                "type": interrupt_type,
                "phase": phase,
            }
            if details is not None:
                interrupt_metadata["details"] = dict(details)
            if resolution is not None:
                interrupt_metadata["resolution"] = resolution
            sequence = stream_state.next_sequence()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=state),
                    final=False,
                    metadata=_build_output_metadata(
                        session_id=session_id,
                        stream={
                            "message_id": stream_state.resolve_message_id(None),
                            "event_id": stream_state.build_event_id(sequence),
                            "source": "interrupt",
                            "sequence": sequence,
                        },
                        interrupt={
                            **interrupt_metadata,
                        },
                    ),
                )
            )
            if phase == "asked":
                _emit_metric("interrupt_requests_total")
            elif phase == "resolved":
                _emit_metric("interrupt_resolved_total")

        async def _emit_progress_status(
            *,
            message_id: str | None,
            progress: Mapping[str, Any],
        ) -> None:
            sequence = stream_state.next_sequence()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.working),
                    final=False,
                    metadata=_build_output_metadata(
                        session_id=session_id,
                        stream={
                            "message_id": stream_state.resolve_message_id(message_id),
                            "event_id": stream_state.build_event_id(sequence),
                            "source": "progress",
                            "sequence": sequence,
                        },
                        progress=dict(progress),
                    ),
                )
            )

        def _new_text_chunk(
            *,
            text: str,
            append: bool,
            block_type: BlockType,
            internal_source: str,
            shared_source: str,
            message_id: str | None,
            role: str | None,
        ) -> _NormalizedStreamChunk:
            return _NormalizedStreamChunk(
                part=Part(root=TextPart(text=text)),
                content_key=text,
                accumulate_content=True,
                append=append,
                block_type=block_type,
                internal_source=internal_source,
                shared_source=shared_source,
                message_id=message_id,
                role=role,
            )

        def _new_data_chunk(
            *,
            data: Mapping[str, Any],
            content_key: str,
            append: bool,
            block_type: BlockType,
            internal_source: str,
            shared_source: str,
            message_id: str | None,
            role: str | None,
        ) -> _NormalizedStreamChunk:
            return _NormalizedStreamChunk(
                part=Part(root=DataPart(data=dict(data))),
                content_key=content_key,
                accumulate_content=False,
                append=append,
                block_type=block_type,
                internal_source=internal_source,
                shared_source=shared_source,
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
            internal_source: str,
        ) -> list[_NormalizedStreamChunk]:
            if not delta_text:
                return []
            if message_id:
                state.message_id = message_id
            state.buffer = f"{state.buffer}{delta_text}"
            state.saw_delta = True
            return [
                _new_text_chunk(
                    text=delta_text,
                    append=True,
                    block_type=state.block_type,
                    internal_source=internal_source,
                    shared_source="stream",
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
                    _new_text_chunk(
                        text=delta_text,
                        append=True,
                        block_type=state.block_type,
                        internal_source="part_text_diff",
                        shared_source="stream",
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
            tool_payload = _extract_tool_part_payload(part)
            if tool_payload is None:
                return []
            content_key = json.dumps(
                tool_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if message_id:
                state.message_id = message_id
            previous = state.buffer
            if content_key == previous:
                return []
            state.buffer = content_key
            return [
                _new_data_chunk(
                    data=tool_payload,
                    content_key=content_key,
                    append=bool(previous),
                    block_type=state.block_type,
                    internal_source="tool_part_update",
                    shared_source="tool_part_update",
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
                        _log_stream_event_debug(
                            event,
                            limit=max(0, self._client.settings.a2a_log_body_limit),
                        )
                        event_type = event.get("type")
                        if not isinstance(event_type, str):
                            continue
                        props = event.get("properties")
                        if not isinstance(props, Mapping):
                            continue
                        event_session_id = _extract_event_session_id(event)
                        if event_session_id == session_id:
                            part = props.get("part")
                            if isinstance(part, Mapping):
                                progress = _extract_progress_metadata(part, props)
                                if progress is not None:
                                    progress_identity = _build_progress_identity(part, props)
                                    progress_key = json.dumps(
                                        progress,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    )
                                    if stream_state.register_progress(
                                        identity=progress_identity,
                                        content_key=progress_key,
                                    ):
                                        await _emit_progress_status(
                                            message_id=_extract_stream_message_id(part, props),
                                            progress=progress,
                                        )
                            upstream_error = _extract_upstream_error_from_event(event)
                            if upstream_error is not None and stream_state.upstream_error is None:
                                stream_state.upstream_error = upstream_error
                            signal = _extract_stream_terminal_signal(event)
                            if signal is not None and not terminal_signal.done():
                                terminal_signal.set_result(signal)
                                stop_event.set()
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
                                        phase="asked",
                                        details=asked["details"],
                                    )
                            resolved = _extract_interrupt_resolved_event(event)
                            if resolved is not None:
                                resolved_request_id = resolved["request_id"]
                                cleared_pending = stream_state.clear_interrupt_pending(
                                    resolved_request_id
                                )
                                discard_request = getattr(
                                    self._client, "discard_interrupt_request", None
                                )
                                if callable(discard_request):
                                    discard_request(resolved_request_id)
                                if cleared_pending:
                                    await _emit_interrupt_status(
                                        state=TaskState.working,
                                        request_id=resolved_request_id,
                                        interrupt_type=resolved["interrupt_type"],
                                        phase="resolved",
                                        resolution=resolved["resolution"],
                                    )
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
                            delta_chunks = _delta_chunks(
                                state=state,
                                delta_text=delta,
                                message_id=message_id,
                                internal_source="delta_event",
                            )
                            if delta_chunks:
                                await _emit_chunks(delta_chunks)
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
                                    internal_source="delta_event_buffered",
                                )
                            )

                        delta = props.get("delta")
                        if isinstance(delta, str) and delta:
                            chunks.extend(
                                _delta_chunks(
                                    state=state,
                                    delta_text=delta,
                                    message_id=message_id,
                                    internal_source="delta",
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
                        else:
                            snapshot_text = _extract_stream_snapshot_text(part)
                            if snapshot_text is not None:
                                chunks.extend(
                                    _snapshot_chunks(
                                        state=state,
                                        snapshot=snapshot_text,
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
                    _emit_metric("opencode_stream_retries_total")
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
        parts=[Part(root=TextPart(text=text))],
        task_id=task_id,
        context_id=context_id,
    )


async def _enqueue_artifact_update(
    *,
    event_queue: EventQueue,
    task_id: str,
    context_id: str,
    artifact_id: str,
    part: Part,
    append: bool | None,
    last_chunk: bool | None,
    artifact_metadata: Mapping[str, Any] | None = None,
    event_metadata: Mapping[str, Any] | None = None,
) -> None:
    normalized_last_chunk = True if last_chunk is True else None
    artifact = Artifact(
        artifact_id=artifact_id,
        parts=[part],
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

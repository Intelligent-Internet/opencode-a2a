from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..server.client_manager import A2AClientManager
    from ..server.state_store import SessionStateRepository

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from ..invocation import call_with_supported_kwargs
from ..opencode_upstream_client import OpencodeUpstreamClient
from ..output_modes import accepts_output_mode, normalize_accepted_output_modes
from ..parts.mapping import (
    UnsupportedA2AInputError,
    extract_text_from_a2a_parts,
    map_a2a_parts_to_opencode_parts,
    summarize_a2a_parts,
)
from ..sandbox_policy import SandboxPolicy
from .coordinator import ExecutionCoordinator, PreparedExecution, build_session_binding_context_id
from .event_helpers import _enqueue_artifact_update
from .metrics import emit_metric
from .request_context import (
    _extract_opencode_directory,
    _extract_opencode_workspace_id,
    _extract_shared_model,
    _extract_shared_session_id,
)
from .session_manager import SessionManager
from .stream_events import (
    BlockType,
    _build_progress_identity,
    _coerce_number,
    _extract_event_session_id,
    _extract_interrupt_asked_event,
    _extract_interrupt_resolved_event,
    _extract_progress_metadata,
    _extract_stream_session_id,
    _extract_stream_snapshot_text,
    _extract_stream_terminal_signal,
    _extract_token_usage,
    _extract_tool_part_payload,
    _extract_upstream_error_from_event,
    _extract_upstream_error_from_response,
    _normalize_interrupt_question_options,
    _normalize_interrupt_questions,
    _normalize_role,
    _preview_log_value,
)
from .stream_runtime import StreamRuntime
from .stream_state import (
    _build_output_metadata,
    _merge_token_usage,
    _StreamOutputState,
    _TTLCache,
)
from .upstream_error_translator import (
    _await_stream_terminal_signal,
    _extract_upstream_error_detail,
    _format_inband_upstream_error,
    _format_stream_terminal_error,
    _format_upstream_error,
    _resolve_upstream_error_profile,
    _StreamTerminalSignal,
)

logger = logging.getLogger(__name__)
_TEXT_PLAIN_MEDIA_TYPE = "text/plain"
_APPLICATION_JSON_MEDIA_TYPE = "application/json"

__all__ = [
    "BlockType",
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
    "_extract_tool_part_payload",
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
    "_TTLCache",
]

_EXPORTED_COMPAT_SYMBOLS = (BlockType, _await_stream_terminal_signal)


def _emit_metric(
    name: str,
    value: float = 1.0,
    **labels: str | int | float | bool,
) -> None:
    emit_metric(name, value, **labels)


class OpencodeAgentExecutor(AgentExecutor):
    def __init__(
        self,
        client: OpencodeUpstreamClient,
        *,
        streaming_enabled: bool,
        cancel_abort_timeout_seconds: float = 2.0,
        session_cache_ttl_seconds: int = 3600,
        session_cache_maxsize: int = 10_000,
        pending_session_claim_ttl_seconds: float = 30.0,
        a2a_client_manager: A2AClientManager | None = None,
        session_state_repository: SessionStateRepository | None = None,
    ) -> None:
        self._client = client
        self._streaming_enabled = streaming_enabled
        self._cancel_abort_timeout_seconds = max(0.0, float(cancel_abort_timeout_seconds))
        self._a2a_client_manager = a2a_client_manager
        self._sandbox_policy = SandboxPolicy.from_settings(
            client.settings,
            workspace_root=client.directory,
        )
        self._session_manager = SessionManager(
            client=client,
            session_cache_ttl_seconds=session_cache_ttl_seconds,
            session_cache_maxsize=session_cache_maxsize,
            pending_session_claim_ttl_seconds=pending_session_claim_ttl_seconds,
            state_repository=session_state_repository,
        )
        self._stream_runtime = StreamRuntime(
            client=client,
            emit_metric=self._emit_metric,
            sleep=asyncio.sleep,
        )
        self._lock = asyncio.Lock()
        self._running_requests: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._running_stop_events: dict[tuple[str, str], asyncio.Event] = {}
        self._running_identities: dict[tuple[str, str], str] = {}
        self._running_session_ids: dict[tuple[str, str], str] = {}
        self._running_directories: dict[tuple[str, str], str | None] = {}
        self._running_workspace_ids: dict[tuple[str, str], str | None] = {}
        self._running_binding_context_ids: dict[tuple[str, str], str] = {}

    @staticmethod
    def _emit_metric(
        name: str,
        value: float = 1.0,
        **labels: str | int | float | bool,
    ) -> None:
        _emit_metric(name, value, **labels)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            await self._emit_error(
                event_queue,
                task_id=task_id or "unknown",
                context_id=context_id or "unknown",
                message="Missing task_id or context_id in request context",
                state=TaskState.TASK_STATE_FAILED,
                streaming_request=self._should_stream(context),
            )
            return

        call_context = context.call_context
        identity = (call_context.state.get("identity") if call_context else None) or "anonymous"
        credential_id = call_context.state.get("credential_id") if call_context else None
        auth_scheme = call_context.state.get("auth_scheme") if call_context else None
        trace_id = call_context.state.get("trace_id") if call_context else None

        streaming_request = self._should_stream(context)
        accepted_output_modes = normalize_accepted_output_modes(context.configuration)
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
                state=TaskState.TASK_STATE_FAILED,
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
                state=TaskState.TASK_STATE_FAILED,
                streaming_request=streaming_request,
            )
            return
        workspace_id = _extract_opencode_workspace_id(context)
        requested_dir = _extract_opencode_directory(context)

        directory: str | None = None
        if workspace_id is None:
            try:
                directory = self._sandbox_policy.resolve_directory(
                    requested_dir,
                    default_directory=self._client.directory,
                )
            except ValueError as e:
                logger.warning("Directory validation failed: %s", e)
                await self._emit_error(
                    event_queue,
                    task_id=task_id,
                    context_id=context_id,
                    message=str(e),
                    state=TaskState.TASK_STATE_FAILED,
                    streaming_request=streaming_request,
                )
                return

        session_binding_context_id = build_session_binding_context_id(
            context_id=context_id,
            directory=directory,
            workspace_id=workspace_id,
            use_directory_binding=requested_dir is not None,
        )

        if not user_text and not request_parts:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Only text and file input are supported.",
                state=TaskState.TASK_STATE_FAILED,
                streaming_request=streaming_request,
            )
            return

        if not accepts_output_mode(accepted_output_modes, _TEXT_PLAIN_MEDIA_TYPE):
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="acceptedOutputModes must include text/plain for OpenCode chat responses.",
                state=TaskState.TASK_STATE_FAILED,
                streaming_request=streaming_request,
            )
            return

        allow_structured_output = accepts_output_mode(
            accepted_output_modes,
            _APPLICATION_JSON_MEDIA_TYPE,
        )

        logger.debug(
            (
                "Received message identity=%s credential_id=%s auth_scheme=%s trace_id=%s "
                "task_id=%s context_id=%s "
                "streaming=%s text_len=%s part_count=%s"
            ),
            identity,
            credential_id,
            auth_scheme,
            trace_id,
            task_id,
            context_id,
            streaming_request,
            len(user_text),
            len(request_parts),
        )
        prepared = PreparedExecution(
            identity=identity,
            streaming_request=streaming_request,
            request_parts=request_parts,
            user_text=user_text,
            session_title=session_title or user_text,
            use_structured_parts=use_structured_parts,
            bound_session_id=bound_session_id,
            model_override=model_override,
            directory=directory,
            workspace_id=workspace_id,
            session_binding_context_id=session_binding_context_id,
            allow_structured_output=allow_structured_output,
        )
        coordinator = ExecutionCoordinator(
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
                    state=TaskState.TASK_STATE_FAILED,
                    streaming_request=False,
                )
                return

            call_context = context.call_context
            identity = (call_context.state.get("identity") if call_context else None) or "anonymous"

            event = TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
            await event_queue.enqueue_event(event)

            execution_key = (task_id, context_id)
            async with self._lock:
                running_identity = self._running_identities.get(execution_key, identity)
                running_task = self._running_requests.get(execution_key)
                stop_event = self._running_stop_events.get(execution_key)
                running_session_id = self._running_session_ids.get(execution_key)
                running_directory = self._running_directories.get(execution_key)
                running_workspace_id = self._running_workspace_ids.get(execution_key)
                running_binding_context_id = self._running_binding_context_ids.get(
                    execution_key,
                    context_id,
                )
            inflight = await self._session_manager.pop_cached_session(
                identity=running_identity,
                context_id=running_binding_context_id,
            )
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
                    abort_kwargs: dict[str, Any] = {"directory": running_directory}
                    if running_workspace_id is not None:
                        abort_kwargs["workspace_id"] = running_workspace_id
                    await asyncio.wait_for(
                        call_with_supported_kwargs(
                            self._client.abort_session,
                            running_session_id,
                            **abort_kwargs,
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
                        state=TaskState.TASK_STATE_FAILED,
                        streaming_request=False,
                    )
        finally:
            self._emit_metric(
                "a2a_cancel_duration_ms",
                (time.monotonic() - started_at) * 1000.0,
                abort_outcome=abort_outcome,
            )

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
            role=Role.ROLE_AGENT,
            parts=[Part(text=message)],
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
                part=Part(text=message),
                append=False,
                last_chunk=True,
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=state),
                    metadata=error_metadata,
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
        return method == "SendStreamingMessage"

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
        workspace_id: str | None = None,
        allow_structured_output: bool = True,
    ) -> None:
        await self._stream_runtime.consume(
            session_id=session_id,
            identity=identity,
            task_id=task_id,
            context_id=context_id,
            artifact_id=artifact_id,
            stream_state=stream_state,
            event_queue=event_queue,
            stop_event=stop_event,
            terminal_signal=terminal_signal,
            directory=directory,
            workspace_id=workspace_id,
            allow_structured_output=allow_structured_output,
        )

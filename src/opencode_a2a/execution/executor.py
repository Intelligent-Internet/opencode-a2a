from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..server.application import A2AClientManager
    from ..server.state_store import SessionStateRepository

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from ..opencode_upstream_client import OpencodeUpstreamClient, UpstreamContractError
from ..parts.mapping import (
    UnsupportedA2AInputError,
    extract_text_from_a2a_parts,
    map_a2a_parts_to_opencode_parts,
    summarize_a2a_parts,
)
from ..sandbox_policy import SandboxPolicy
from .event_helpers import _enqueue_artifact_update
from .request_context import (
    _build_history,
    _extract_opencode_directory,
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
    _build_stream_artifact_metadata,
    _merge_token_usage,
    _StreamOutputState,
    _TTLCache,
)
from .tool_error_mapping import build_tool_error, map_a2a_tool_exception
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

            turn_request_parts = list(self._prepared.request_parts)
            user_text = self._prepared.user_text

            while True:
                send_kwargs: dict[str, Any] = {
                    "directory": self._prepared.directory,
                    "model_override": self._prepared.model_override,
                }
                if self._prepared.streaming_request:
                    send_kwargs["timeout_override"] = self._executor._client.stream_timeout

                if not self._prepared.use_structured_parts and not turn_request_parts:
                    response = await self._executor._client.send_message(
                        self._session_id,
                        user_text,
                        **send_kwargs,
                    )
                else:
                    response = await self._executor._client.send_message(
                        self._session_id,
                        user_text or None,
                        parts=turn_request_parts,
                        **send_kwargs,
                    )

                if self._pending_preferred_claim:
                    await self._executor._session_manager.finalize_preferred_session_binding(
                        identity=self._prepared.identity,
                        context_id=self._context_id,
                        session_id=self._session_id,
                    )
                    self._pending_preferred_claim = False

                # Check for tool calls that we should handle
                tool_results = await self._executor._maybe_handle_tools(response.raw)
                if tool_results:
                    # Clear user_text/parts for the next turn with tool results.
                    user_text = ""
                    turn_request_parts = [
                        {
                            "type": "tool",
                            "tool": res["tool"],
                            "call_id": res["call_id"],
                            "output": res.get("output"),
                            "error": res.get("error"),
                        }
                        for res in tool_results
                    ]
                    # Loop back to send tool results
                    continue

                await self._handle_response(response)
                break

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
        ) = await self._executor._session_manager.get_or_create_session(
            self._prepared.identity,
            self._context_id,
            self._prepared.session_title or self._prepared.user_text,
            preferred_session_id=self._prepared.bound_session_id,
            directory=self._prepared.directory,
        )
        self._session_lock = await self._executor._session_manager.get_session_lock(
            self._session_id
        )
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
                await self._executor._session_manager.release_preferred_session_claim(
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

    @staticmethod
    def _emit_metric(
        name: str,
        value: float = 1.0,
        **labels: str | int | float | bool,
    ) -> None:
        _emit_metric(name, value, **labels)

    async def _maybe_handle_tools(
        self, raw_response: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Heuristically detect and execute A2A tool calls from upstream OpenCode."""
        parts = raw_response.get("parts", [])
        if not isinstance(parts, list):
            return None

        results: list[dict[str, Any]] = []
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue

            state = part.get("state")
            if not isinstance(state, dict) or state.get("status") != "calling":
                continue

            tool_name = part.get("tool")
            if tool_name == "a2a_call":
                result = await self._handle_a2a_call_tool(part)
                if result:
                    results.append(result)

        return results if results else None

    async def _handle_a2a_call_tool(self, part: dict[str, Any]) -> dict[str, Any]:
        call_id = part.get("callID") or str(uuid.uuid4())
        tool_name = part.get("tool") or "a2a_call"
        state = part.get("state", {})
        inputs = state.get("input", {})

        if not isinstance(inputs, dict):
            return {
                "call_id": call_id,
                "tool": tool_name,
                **build_tool_error(
                    error_code="a2a_invalid_input",
                    error="Invalid a2a_call input payload",
                ),
            }

        agent_url = inputs.get("url")
        message = inputs.get("message")
        if not agent_url or not message:
            return {
                "call_id": call_id,
                "tool": tool_name,
                **build_tool_error(
                    error_code="a2a_missing_required_input",
                    error="Missing required a2a_call url or message",
                ),
            }

        mgr = self._a2a_client_manager
        if mgr is None:
            return {
                "call_id": call_id,
                "tool": tool_name,
                **build_tool_error(
                    error_code="a2a_client_manager_unavailable",
                    error="A2A client manager is not available",
                ),
            }

        try:
            event = None
            result_text = ""
            async with mgr.borrow_client(agent_url) as client:
                async for current_event in client.send_message(message):
                    event = current_event
                    extracted = client.extract_text(current_event)
                    if extracted:
                        result_text = self._merge_streamed_tool_output(result_text, extracted)

            from a2a.types import Task

            if result_text:
                return {
                    "call_id": call_id,
                    "tool": tool_name,
                    "output": result_text,
                }

            if isinstance(event, Task):
                result_text = ""
                # Extract text from Task's assistant message if available
                if event.status and event.status.message:
                    for part_obj in event.status.message.parts:
                        # Use dict-style access if available or getattr to satisfy type checkers
                        root = getattr(part_obj, "root", part_obj)
                        text_val = getattr(root, "text", "")
                        if text_val:
                            result_text += str(text_val)
                return {
                    "call_id": call_id,
                    "tool": tool_name,
                    "output": result_text or "Task completed.",
                }

            # Handle case where event is a tuple (Task, Update)
            if isinstance(event, tuple) and len(event) > 0 and isinstance(event[0], Task):
                return {
                    "call_id": call_id,
                    "tool": tool_name,
                    "output": "Task completed (streaming).",
                }

            return {
                "call_id": call_id,
                "tool": tool_name,
                **build_tool_error(
                    error_code="a2a_unexpected_response",
                    error="Remote A2A peer returned an unexpected response type",
                    error_meta={"response_type": type(event).__name__},
                ),
            }
        except Exception as exc:
            logger.exception("A2A tool call failed")
            return {
                "call_id": call_id,
                "tool": tool_name,
                **map_a2a_tool_exception(exc),
            }

    @staticmethod
    def _merge_streamed_tool_output(current: str, incoming: str) -> str:
        if not current:
            return incoming
        if incoming == current or incoming in current:
            return current
        if incoming.startswith(current):
            return incoming
        if current.startswith(incoming):
            return current
        separator = (
            ""
            if current.endswith(("\n", " ", "\t")) or incoming.startswith(("\n", " ", "\t"))
            else "\n"
        )
        return f"{current}{separator}{incoming}"

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
            inflight = await self._session_manager.pop_cached_session(
                identity=running_identity,
                context_id=context_id,
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
        )


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

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from a2a.server.agent_execution import RequestContext
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
)

from ..opencode_upstream_client import UpstreamConcurrencyLimitError, UpstreamContractError
from .event_helpers import _enqueue_artifact_update
from .stream_events import _extract_token_usage, _extract_upstream_error_from_response
from .stream_state import (
    _build_output_metadata,
    _build_stream_artifact_metadata,
    _merge_token_usage,
    _StreamOutputState,
)
from .tool_orchestration import maybe_handle_tools
from .upstream_error_translator import (
    _await_stream_terminal_signal,
    _format_upstream_error,
    _StreamTerminalSignal,
)

if TYPE_CHECKING:
    from .executor import OpencodeAgentExecutor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedExecution:
    identity: str
    streaming_request: bool
    request_parts: list[Any]
    user_text: str
    session_title: str
    use_structured_parts: bool
    bound_session_id: str | None
    model_override: dict[str, str] | None
    directory: str | None
    workspace_id: str | None
    session_binding_context_id: str
    allow_structured_output: bool
    emit_session_metadata: bool
    emit_streaming_metadata: bool


def build_session_binding_context_id(
    *,
    context_id: str,
    directory: str | None,
    workspace_id: str | None,
    use_directory_binding: bool,
) -> str:
    if isinstance(workspace_id, str) and workspace_id.strip():
        return f"{context_id}::workspace:{workspace_id.strip()}"
    if use_directory_binding and isinstance(directory, str) and directory.strip():
        return f"{context_id}::directory:{directory.strip()}"
    return context_id


class ExecutionCoordinator:
    def __init__(
        self,
        executor: OpencodeAgentExecutor,
        *,
        context: RequestContext,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        prepared: PreparedExecution,
    ) -> None:
        self._executor = executor
        self._context = context
        self._event_queue = event_queue
        self._task_id = task_id
        self._context_id = context_id
        self._prepared = prepared
        self._stream_artifact_base_id = f"{task_id}:stream"
        self._stream_text_artifact_id = f"{self._stream_artifact_base_id}:text"
        self._stream_state = _StreamOutputState(
            user_text=prepared.user_text,
            stable_message_id=f"{task_id}:{context_id}:assistant",
            event_id_namespace=f"{task_id}:{context_id}:{self._stream_artifact_base_id}",
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
            await self._event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=self._task_id,
                    context_id=self._context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )

            turn_request_parts = list(self._prepared.request_parts)
            user_text = self._prepared.user_text

            while True:
                send_kwargs: dict[str, Any] = {}
                if self._prepared.directory is not None:
                    send_kwargs["directory"] = self._prepared.directory
                if self._prepared.workspace_id is not None:
                    send_kwargs["workspace_id"] = self._prepared.workspace_id
                if self._prepared.model_override is not None:
                    send_kwargs["model_override"] = self._prepared.model_override
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
                        context_id=self._prepared.session_binding_context_id,
                        session_id=self._session_id,
                    )
                    self._pending_preferred_claim = False

                tool_results = await maybe_handle_tools(
                    response.raw,
                    a2a_client_manager=self._executor._a2a_client_manager,
                )
                if tool_results:
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
                    continue

                await self._handle_response(response)
                break

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "OpenCode request failed with HTTP status=%s",
                exc.response.status_code,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
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
            logger.warning(
                "OpenCode request timed out: %s",
                exc,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=f"OpenCode request timed out: {exc}",
                state=TaskState.TASK_STATE_FAILED,
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
                state=TaskState.TASK_STATE_FAILED,
                error_type="UPSTREAM_PAYLOAD_ERROR",
                streaming_request=self._prepared.streaming_request,
            )
        except UpstreamConcurrencyLimitError as exc:
            logger.debug("OpenCode request rejected by concurrency budget: %s", exc)
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=str(exc),
                state=TaskState.TASK_STATE_FAILED,
                error_type="UPSTREAM_BACKPRESSURE",
                streaming_request=self._prepared.streaming_request,
            )
        except Exception as exc:
            logger.exception("OpenCode request failed")
            await self._executor._emit_error(
                self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                message=f"OpenCode error: {exc}",
                state=TaskState.TASK_STATE_FAILED,
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
            self._prepared.session_binding_context_id,
            self._prepared.session_title or self._prepared.user_text,
            preferred_session_id=self._prepared.bound_session_id,
            directory=self._prepared.directory,
            workspace_id=self._prepared.workspace_id,
        )
        self._session_lock = await self._executor._session_manager.get_session_lock(
            self._session_id
        )
        await self._session_lock.acquire()
        async with self._executor._lock:
            self._executor._running_session_ids[self._execution_key] = self._session_id
            self._executor._running_directories[self._execution_key] = self._prepared.directory
            self._executor._running_workspace_ids[self._execution_key] = self._prepared.workspace_id
            self._executor._running_binding_context_ids[self._execution_key] = (
                self._prepared.session_binding_context_id
            )

        if self._prepared.streaming_request:
            self._stream_terminal_signal = asyncio.get_running_loop().create_future()
            self._stream_task = asyncio.create_task(
                self._executor._consume_opencode_stream(
                    session_id=self._session_id,
                    identity=self._prepared.identity,
                    task_id=self._task_id,
                    context_id=self._context_id,
                    artifact_id=self._stream_artifact_base_id,
                    stream_state=self._stream_state,
                    event_queue=self._event_queue,
                    stop_event=self._stop_event,
                    directory=self._prepared.directory,
                    workspace_id=self._prepared.workspace_id,
                    terminal_signal=self._stream_terminal_signal,
                    allow_structured_output=self._prepared.allow_structured_output,
                    emit_session_metadata=self._prepared.emit_session_metadata,
                    emit_streaming_metadata=self._prepared.emit_streaming_metadata,
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
            "OpenCode response task_id=%s session_id=%s message_id=%s text_len=%s",
            self._task_id,
            response.session_id,
            resolved_message_id,
            len(response_text),
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
        from .stream_events import BlockType

        del response
        if self._stream_terminal_signal is None:
            raise RuntimeError("Streaming terminal signal was not initialized")

        terminal_signal = await _await_stream_terminal_signal(
            stream_task=self._stream_task,
            terminal_signal=self._stream_terminal_signal,
            session_id=self._session_id,
        )
        if terminal_signal.state != TaskState.TASK_STATE_COMPLETED:
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

        final_text_artifact_id = self._stream_state.resolve_text_artifact_id(
            self._stream_text_artifact_id
        )
        if self._stream_state.should_emit_final_snapshot(
            artifact_id=final_text_artifact_id,
            text=response_text,
        ):
            sequence = self._stream_state.next_sequence()
            await _enqueue_artifact_update(
                event_queue=self._event_queue,
                task_id=self._task_id,
                context_id=self._context_id,
                artifact_id=final_text_artifact_id,
                part=Part(text=response_text),
                append=False,
                last_chunk=True,
                artifact_metadata=_build_stream_artifact_metadata(
                    block_type=BlockType.TEXT,
                    shared_source="final_snapshot",
                    part_id=None,
                    message_id=resolved_message_id,
                    event_id=self._stream_state.build_event_id(sequence),
                    sequence=sequence,
                    include_shared_stream_metadata=self._prepared.emit_streaming_metadata,
                ),
            )

        await self._event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=self._task_id,
                context_id=self._context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                metadata=_build_output_metadata(
                    session_id=self._session_id,
                    usage=resolved_token_usage,
                    stream={
                        "message_id": resolved_message_id,
                        "event_id": f"{self._stream_state.event_id_namespace}:status",
                        "source": "status",
                    },
                    include_session_metadata=self._prepared.emit_session_metadata,
                    include_streaming_metadata=self._prepared.emit_streaming_metadata,
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
        assistant_message = Message(
            message_id=resolved_message_id or str(uuid.uuid4()),
            role=Role.ROLE_AGENT,
            parts=[Part(text=response_text)],
            task_id=self._task_id,
            context_id=self._context_id,
        )
        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            name="response",
            parts=[Part(text=response_text)],
        )
        if self._context.current_task and self._context.current_task.history:
            history = list(self._context.current_task.history)
        else:
            history = []
            if self._context.message:
                history.append(self._context.message)
        task = Task(
            id=self._task_id,
            context_id=self._context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            history=history,
            artifacts=[artifact],
            metadata=_build_output_metadata(
                session_id=response.session_id,
                usage=resolved_token_usage,
                include_session_metadata=self._prepared.emit_session_metadata,
                include_streaming_metadata=self._prepared.emit_streaming_metadata,
            ),
        )
        task.status.message.CopyFrom(assistant_message)
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
            self._executor._running_workspace_ids.pop(self._execution_key, None)
            self._executor._running_binding_context_ids.pop(self._execution_key, None)

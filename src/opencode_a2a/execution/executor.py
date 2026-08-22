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

from ..contracts.extensions import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
)
from ..extension_negotiation import requested_extensions_from_call_context
from ..metadata_access import extract_first_namespaced_string
from ..opencode_upstream_client import OpencodeUpstreamClient
from ..output_modes import accepts_output_mode, normalize_accepted_output_modes
from ..parts.mapping import (
    UnsupportedA2AInputError,
    extract_text_from_a2a_parts,
    map_a2a_parts_to_opencode_parts,
    summarize_a2a_parts,
)
from ..redact import redact_absolute_paths
from ..sandbox_policy import SandboxPolicy
from .coordinator import ExecutionCoordinator, PreparedExecution, build_session_binding_context_id
from .event_helpers import _enqueue_artifact_update
from .metrics import emit_metric
from .session_manager import SessionManager
from .stream_runtime import StreamRuntime

logger = logging.getLogger(__name__)
_TEXT_PLAIN_MEDIA_TYPE = "text/plain"
_APPLICATION_JSON_MEDIA_TYPE = "application/json"


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
            emit_metric=emit_metric,
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
        configuration = context.configuration
        return_immediately = (
            configuration is not None
            and getattr(configuration, "return_immediately", False) is True
        )
        requested_extensions = requested_extensions_from_call_context(context.call_context)
        accepted_output_modes = normalize_accepted_output_modes(configuration)
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
        try:
            context_meta = context.metadata
        except Exception:
            context_meta = None

        metadata_sources: list[Mapping[str, Any] | None] = []
        if isinstance(context_meta, Mapping):
            metadata_sources.append(context_meta)
        if context.message is not None:
            message_meta = getattr(context.message, "metadata", None) or {}
            if isinstance(message_meta, Mapping):
                metadata_sources.append(message_meta)
        metadata_source_tuple = tuple(metadata_sources)

        bound_session_id = extract_first_namespaced_string(
            metadata_source_tuple,
            namespace="shared",
            path=("session", "id"),
        )
        model_provider_id = extract_first_namespaced_string(
            metadata_source_tuple,
            namespace="shared",
            path=("model", "providerID"),
        )
        model_id = extract_first_namespaced_string(
            metadata_source_tuple,
            namespace="shared",
            path=("model", "modelID"),
        )
        model_override = (
            {"providerID": model_provider_id, "modelID": model_id}
            if model_provider_id is not None and model_id is not None
            else None
        )
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
        workspace_id = extract_first_namespaced_string(
            metadata_source_tuple,
            namespace="opencode",
            path=("workspace", "id"),
        )
        requested_dir = extract_first_namespaced_string(
            metadata_source_tuple,
            namespace="opencode",
            path=("directory",),
        )

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
        emit_session_metadata = SESSION_BINDING_EXTENSION_URI in requested_extensions
        emit_streaming_metadata = STREAMING_EXTENSION_URI in requested_extensions
        emit_interrupt_metadata = INTERRUPT_CALLBACK_EXTENSION_URI in requested_extensions

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
            return_immediately=return_immediately,
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
            emit_session_metadata=emit_session_metadata,
            emit_streaming_metadata=emit_streaming_metadata,
            emit_interrupt_metadata=emit_interrupt_metadata,
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
        emit_metric("a2a_cancel_requests_total")
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
                emit_metric("a2a_cancel_abort_attempt_total")
                try:
                    abort_kwargs: dict[str, Any] = {"directory": running_directory}
                    if running_workspace_id is not None:
                        abort_kwargs["workspace_id"] = running_workspace_id
                    await asyncio.wait_for(
                        self._client.abort_session(running_session_id, **abort_kwargs),
                        timeout=self._cancel_abort_timeout_seconds,
                    )
                    abort_outcome = "success"
                    emit_metric("a2a_cancel_abort_success_total")
                except TimeoutError:
                    abort_outcome = "timeout"
                    emit_metric("a2a_cancel_abort_timeout_total")
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
                    emit_metric("a2a_cancel_abort_error_total")
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
            emit_metric("a2a_cancel_errors_total")
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
            emit_metric(
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
        message = redact_absolute_paths(message)
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

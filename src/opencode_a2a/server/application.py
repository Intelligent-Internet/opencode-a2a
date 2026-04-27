from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import uvicorn
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventConsumer, EventQueueLegacy
from a2a.server.request_handlers.default_request_handler import (
    TERMINAL_TASK_STATES,
    LegacyRequestHandler,
)
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.rest_dispatcher import RestDispatcher
from a2a.server.tasks import ResultAggregator, TaskManager
from a2a.types import (
    AgentCard,
    Artifact,
    CancelTaskRequest,
    GetTaskRequest,
    InternalError,
    InvalidRequestError,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
)
from a2a.utils import proto_utils
from a2a.utils.errors import (
    A2A_REST_ERROR_MAPPING,
    A2AError,
    RestErrorMap,
)
from a2a.utils.task import apply_history_length, validate_history_length
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from pydantic_settings import BaseSettings
from starlette.middleware.gzip import GZipMiddleware

from ..a2a_protocol import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)
from ..config import Settings
from ..contracts.extensions import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    INTERRUPT_RECOVERY_METHODS,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_CONTROL_METHODS,
    SESSION_MANAGEMENT_EXTENSION_URI,
    SESSION_METHODS,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
    WORKSPACE_CONTROL_METHODS,
    build_capability_snapshot,
)
from ..execution.executor import OpencodeAgentExecutor
from ..invocation import call_with_supported_kwargs
from ..jsonrpc.application import (
    OpencodeSessionManagementJSONRPCApplication,
)
from ..jsonrpc.error_responses import build_http_error_body
from ..opencode_upstream_client import OpencodeUpstreamClient
from ..output_modes import (
    NegotiatingResultAggregator,
    apply_accepted_output_modes,
    extract_accepted_output_modes_from_metadata,
    normalize_accepted_output_modes,
)
from ..profile.runtime import build_runtime_profile
from ..trace_context import install_log_record_factory
from .agent_card import (
    _CHAT_OUTPUT_MODES,
    _build_agent_card_description,
    _build_chat_examples,
    _build_session_management_skill_examples,
    build_agent_card,
    build_authenticated_extended_agent_card,
)
from .client_manager import A2AClientManager
from .lifespan import build_lifespan
from .middleware import (
    AUTHENTICATED_EXTENDED_CARD_CACHE_CONTROL,
    PUBLIC_AGENT_CARD_CACHE_CONTROL,
    build_agent_card_etag,
    emit_stream_request_metrics,
    install_runtime_middlewares,
)
from .openapi import (
    _build_jsonrpc_extension_openapi_description,
    _build_jsonrpc_extension_openapi_examples,
    _build_rest_message_openapi_examples,
    _patch_jsonrpc_openapi_contract,
)
from .request_parsing import (
    _decode_payload_preview,
    _detect_sensitive_extension_method,
    _is_json_content_type,
    _looks_like_jsonrpc_envelope,
    _normalize_content_type,
    _parse_content_length,
    _parse_json_body,
    _request_body_too_large_response,
    _RequestBodyTooLargeError,
)
from .rest_tasks import build_list_tasks_route
from .state_store import (
    build_interrupt_request_repository,
    build_session_state_repository,
)
from .task_store import (
    TaskStoreOperationError,
    build_database_engine,
    build_task_store,
    describe_lightweight_persistence_backend,
)

logger = logging.getLogger(__name__)
TASK_STORE_ERROR_TYPE = "TASK_STORE_UNAVAILABLE"
PUSH_NOTIFICATIONS_UNSUPPORTED_MESSAGE = "Push notifications are not supported by the agent"


def _are_modalities_compatible(
    supported_output_modes: list[str],
    accepted_output_modes: list[str],
) -> bool:
    return bool(set(supported_output_modes) & set(accepted_output_modes))


def _rest_error_response(
    *,
    request: Request,
    default_protocol_version: str,
    error: Exception,
) -> JSONResponse:
    protocol_version = getattr(
        request.state,
        "a2a_protocol_version",
        default_protocol_version,
    )
    logger_fn = logger.exception
    logger_message = "Unexpected REST message route failure"

    if isinstance(error, A2AError):
        mapping = A2A_REST_ERROR_MAPPING.get(
            type(error),
            RestErrorMap(500, "INTERNAL", "INTERNAL_ERROR"),
        )
        message = getattr(error, "message", str(error))
        metadata = getattr(error, "data", None) or {}
        logger_fn = logger.error if mapping.http_code >= 500 else logger.warning
        logger_message = (
            f"REST message route failed status={mapping.http_code} "
            f"reason={mapping.reason} message={message}"
        )
        logger_fn(logger_message)
        return JSONResponse(
            build_http_error_body(
                protocol_version=protocol_version,
                status_code=mapping.http_code,
                status=mapping.grpc_status,
                message=message,
                legacy_payload={"error": message},
                reason=mapping.reason,
                metadata=metadata,
            ),
            status_code=mapping.http_code,
        )

    if isinstance(error, ParseError):
        message = str(error)
        logger_fn = logger.warning
        logger_message = f"REST message payload parse error: {message}"
        logger_fn(logger_message)
        return JSONResponse(
            build_http_error_body(
                protocol_version=protocol_version,
                status_code=400,
                status="INVALID_ARGUMENT",
                message=message,
                legacy_payload={"error": message},
                reason="INVALID_REQUEST",
            ),
            status_code=400,
        )

    logger_fn(logger_message)
    return JSONResponse(
        build_http_error_body(
            protocol_version=protocol_version,
            status_code=500,
            status="INTERNAL",
            message="unknown exception",
            legacy_payload={"error": "unknown exception"},
            reason="INTERNAL_ERROR",
        ),
        status_code=500,
    )


def _parse_rest_send_message_request(body: bytes):
    payload = _parse_json_body(body)
    if payload is None:
        raise InvalidRequestError(message="REST message payload must be a JSON object.")
    message = payload.get("message")
    if isinstance(message, dict):
        if "content" in message:
            raise InvalidRequestError(
                message="REST message payload must use message.parts, not message.content."
            )
        role = message.get("role")
        if isinstance(role, str) and role in {"user", "agent"}:
            raise InvalidRequestError(
                message="REST message payload must use ROLE_* values for message.role."
            )
        parts = message.get("parts")
        if isinstance(parts, list):
            for index, part in enumerate(parts):
                if isinstance(part, dict) and ("kind" in part or "type" in part or "file" in part):
                    raise InvalidRequestError(
                        message=(
                            f"message.parts[{index}] must use direct Part fields "
                            "such as text, raw, url, or data."
                        )
                    )
    return ParseDict(payload, SendMessageRequest())


__all__ = [
    "_RequestBodyTooLargeError",
    "COMPATIBILITY_PROFILE_EXTENSION_URI",
    "INTERRUPT_CALLBACK_EXTENSION_URI",
    "INTERRUPT_CALLBACK_METHODS",
    "INTERRUPT_RECOVERY_EXTENSION_URI",
    "INTERRUPT_RECOVERY_METHODS",
    "MODEL_SELECTION_EXTENSION_URI",
    "PUBLIC_AGENT_CARD_CACHE_CONTROL",
    "AUTHENTICATED_EXTENDED_CARD_CACHE_CONTROL",
    "PROVIDER_DISCOVERY_EXTENSION_URI",
    "PROVIDER_DISCOVERY_METHODS",
    "SESSION_MANAGEMENT_EXTENSION_URI",
    "SESSION_BINDING_EXTENSION_URI",
    "SESSION_CONTROL_METHODS",
    "SESSION_METHODS",
    "STREAMING_EXTENSION_URI",
    "WIRE_CONTRACT_EXTENSION_URI",
    "WORKSPACE_CONTROL_EXTENSION_URI",
    "WORKSPACE_CONTROL_METHODS",
    "_build_agent_card_description",
    "_build_chat_examples",
    "_build_jsonrpc_extension_openapi_description",
    "_build_jsonrpc_extension_openapi_examples",
    "_build_rest_message_openapi_examples",
    "_build_session_management_skill_examples",
    "build_authenticated_extended_agent_card",
    "_configure_logging",
    "_decode_payload_preview",
    "_detect_sensitive_extension_method",
    "_is_json_content_type",
    "_looks_like_jsonrpc_envelope",
    "_normalize_content_type",
    "_normalize_log_level",
    "_parse_content_length",
    "_parse_json_body",
    "_request_body_too_large_response",
    "build_agent_card",
]

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from a2a.server.agent_execution import AgentExecutor, RequestContextBuilder
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks import (
        PushNotificationConfigStore,
        PushNotificationSender,
        TaskStore,
    )


class OpencodeRequestHandler(LegacyRequestHandler):
    """Custom request handler to gracefully handle client disconnects and prevent dead loops."""

    def __init__(  # noqa: PLR0913
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        agent_card: AgentCard,
        queue_manager: Any | None = None,
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        request_context_builder: RequestContextBuilder | None = None,
        extended_agent_card: AgentCard | None = None,
        extended_card_modifier: Callable[[AgentCard, ServerCallContext], Awaitable[AgentCard]]
        | None = None,
    ) -> None:
        super().__init__(
            agent_executor=agent_executor,
            task_store=task_store,
            agent_card=agent_card,
            queue_manager=queue_manager,
            push_config_store=push_config_store,
            push_sender=push_sender,
            request_context_builder=request_context_builder,
            extended_agent_card=extended_agent_card,
            extended_card_modifier=extended_card_modifier,
        )

    @staticmethod
    def _task_store_failure_message(operation: str) -> str:
        if operation == "get":
            return "Task store unavailable while loading task state."
        if operation == "save":
            return "Task store unavailable while persisting task state."
        if operation == "delete":
            return "Task store unavailable while deleting task state."
        return "Task store unavailable."

    @classmethod
    def _task_store_failure_metadata(cls, operation: str) -> dict[str, dict[str, dict[str, str]]]:
        return {
            "opencode": {
                "error": {
                    "type": TASK_STORE_ERROR_TYPE,
                    "operation": operation,
                }
            }
        }

    @classmethod
    def _task_store_server_error(cls, exc: TaskStoreOperationError) -> InternalError:
        return InternalError(message=cls._task_store_failure_message(exc.operation))

    @classmethod
    def _task_store_failure_task(
        cls,
        *,
        task_id: str,
        context_id: str,
        operation: str,
    ) -> Task:
        message_text = cls._task_store_failure_message(operation)
        error_message = Message(
            message_id=f"{task_id}:task-store-error",
            role=Role.ROLE_AGENT,
            parts=[Part(text=message_text)],
            task_id=task_id,
            context_id=context_id,
        )
        return Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_FAILED, message=error_message),
            history=[error_message],
            metadata=cls._task_store_failure_metadata(operation),
        )

    @classmethod
    def _task_store_failure_events(
        cls,
        *,
        task_id: str,
        context_id: str,
        operation: str,
    ) -> tuple[TaskArtifactUpdateEvent, TaskStatusUpdateEvent]:
        message_text = cls._task_store_failure_message(operation)
        return (
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=Artifact(
                    artifact_id=f"{task_id}:error",
                    parts=[Part(text=message_text)],
                ),
                append=False,
                last_chunk=True,
            ),
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
                metadata=cls._task_store_failure_metadata(operation),
            ),
        )

    @staticmethod
    def _resolve_context_id_from_params(params, task_id: str) -> str:  # noqa: ANN001
        message = getattr(params, "message", None)
        return (
            getattr(message, "contextId", None) or getattr(message, "context_id", None) or task_id
        )

    @staticmethod
    def _extract_accepted_output_modes(params) -> list[str] | None:  # noqa: ANN001
        configuration = getattr(params, "configuration", None)
        normalized = normalize_accepted_output_modes(configuration)
        return list(normalized) if normalized is not None else None

    @staticmethod
    def _apply_task_output_negotiation(task: Task) -> Task:
        negotiated = apply_accepted_output_modes(
            task,
            extract_accepted_output_modes_from_metadata(task.metadata),
        )
        if isinstance(negotiated, Task):
            return negotiated
        return task

    async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
        (
            task_manager,
            task_id,
            queue,
            _result_aggregator,
            producer_task,
        ) = await super()._setup_message_execution(params, context)
        accepted_output_modes = self._extract_accepted_output_modes(params)
        return (
            task_manager,
            task_id,
            queue,
            NegotiatingResultAggregator(task_manager, accepted_output_modes),
            producer_task,
        )

    @classmethod
    def _validate_chat_output_modes(cls, params) -> None:  # noqa: ANN001
        accepted_output_modes = cls._extract_accepted_output_modes(params)
        if not accepted_output_modes:
            return

        if not _are_modalities_compatible(list(_CHAT_OUTPUT_MODES), accepted_output_modes):
            raise UnsupportedOperationError(
                message=(
                    "Requested acceptedOutputModes are not compatible with OpenCode chat responses."
                ),
                data={
                    "accepted_output_modes": accepted_output_modes,
                    "supported_output_modes": list(_CHAT_OUTPUT_MODES),
                },
            )

        if "text/plain" not in accepted_output_modes:
            raise UnsupportedOperationError(
                message="OpenCode chat responses require text/plain in acceptedOutputModes.",
                data={
                    "accepted_output_modes": accepted_output_modes,
                    "required_output_modes": ["text/plain"],
                    "supported_output_modes": list(_CHAT_OUTPUT_MODES),
                },
            )

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context=None,
    ) -> Task | None:
        try:
            validate_history_length(params)
            task = await self.task_store.get(params.id, context)
            if not task:
                raise TaskNotFoundError()
            return self._apply_task_output_negotiation(apply_history_length(task, params))
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context=None,
    ) -> Task | None:
        try:
            task = await self.task_store.get(params.id, context)
            if not task:
                raise TaskNotFoundError()

            # Idempotent contract:
            # repeated cancel on already-canceled task returns current terminal state.
            if task.status.state == TaskState.TASK_STATE_CANCELED:
                return task

            if task.status.state in TERMINAL_TASK_STATES:
                raise TaskNotCancelableError(
                    message=f"Task cannot be canceled - current state: {task.status.state}"
                )
            try:
                task_manager = TaskManager(
                    task_id=task.id,
                    context_id=task.context_id,
                    task_store=self.task_store,
                    initial_message=None,
                    context=context,
                )
                result_aggregator = ResultAggregator(task_manager)
                queue = await self._queue_manager.tap(task.id)
                if not queue:
                    queue = EventQueueLegacy()

                await self.agent_executor.cancel(
                    RequestContext(
                        call_context=context,
                        request=None,
                        task_id=task.id,
                        context_id=task.context_id,
                        task=task,
                    ),
                    queue,
                )
                if producer_task := self._running_agents.get(task.id):
                    producer_task.cancel()

                result = await result_aggregator.consume_all(EventConsumer(queue))
                if not isinstance(result, Task):
                    raise InternalError(message="Agent did not return valid response for cancel")
                if result.status.state != TaskState.TASK_STATE_CANCELED:
                    raise TaskNotCancelableError(
                        message=f"Task cannot be canceled - current state: {result.status.state}"
                    )
                return result
            except TaskNotCancelableError:
                refreshed = await self.task_store.get(params.id, context)
                if refreshed and refreshed.status.state == TaskState.TASK_STATE_CANCELED:
                    return refreshed
                raise
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context=None,
    ):
        try:
            task = await self.task_store.get(params.id, context)
            if not task:
                raise TaskNotFoundError()

            # Subscribe contract: terminal tasks replay once and then close stream.
            if task.status.state in TERMINAL_TASK_STATES:
                yield self._apply_task_output_negotiation(task)
                return

            yield self._apply_task_output_negotiation(task)

            task_manager = TaskManager(
                task_id=task.id,
                context_id=task.context_id,
                task_store=self.task_store,
                initial_message=None,
                context=context,
            )
            result_aggregator = ResultAggregator(task_manager)
            queue = await self._queue_manager.tap(task.id)
            if not queue:
                raise TaskNotFoundError()

            async for event in result_aggregator.consume_and_emit(EventConsumer(queue)):
                negotiated = apply_accepted_output_modes(
                    event,
                    extract_accepted_output_modes_from_metadata(getattr(event, "metadata", None)),
                )
                if negotiated is not None:
                    yield negotiated
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc

    async def on_message_send_stream(self, params, context=None):
        self._validate_chat_output_modes(params)
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)
        emit_stream_request_metrics()
        emit_stream_request_metrics(active_delta=1.0)
        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)
        stream_completed = False

        try:
            async for event in result_aggregator.consume_and_emit(consumer):
                if hasattr(event, "id") and event.id:
                    self._validate_task_id_match(task_id, event.id)
                await self._send_push_notification_if_needed(task_id, result_aggregator)
                yield event
            stream_completed = True
        except TaskStoreOperationError as exc:
            logger.exception(
                "Task store operation failed during streaming task_id=%s operation=%s",
                task_id,
                exc.operation,
            )
            for event in self._task_store_failure_events(
                task_id=task_id,
                context_id=self._resolve_context_id_from_params(params, task_id),
                operation=exc.operation,
            ):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            logger.debug("Client disconnected. Cancelling producer task %s", task_id)
            producer_task.cancel()
            await queue.close(immediate=True)
            raise
        finally:
            emit_stream_request_metrics(active_delta=-1.0)
            logger.debug(
                "A2A stream request closed task_id=%s completed=%s",
                task_id,
                stream_completed,
            )
            cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
            cleanup_task.set_name(f"cleanup_producer:{task_id}")
            self._track_background_task(cleanup_task)

    async def on_message_send(self, params, context=None):
        self._validate_chat_output_modes(params)
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)

        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        blocking = True
        if params.configuration:
            blocking = not params.configuration.return_immediately

        interrupted_or_non_blocking = False
        bg_consume_task: asyncio.Task | None = None
        try:

            async def push_notification_callback() -> None:
                await self._send_push_notification_if_needed(task_id, result_aggregator)

            (
                result,
                interrupted_or_non_blocking,
                bg_consume_task,
            ) = await result_aggregator.consume_and_break_on_interrupt(
                consumer,
                blocking=blocking,
                event_callback=push_notification_callback,
            )
            if bg_consume_task is not None:
                bg_consume_task.set_name(f"continue_consuming:{task_id}")
                self._track_background_task(bg_consume_task)
        except TaskStoreOperationError as exc:
            logger.exception(
                "Task store operation failed during SendMessage task_id=%s operation=%s",
                task_id,
                exc.operation,
            )
            return self._task_store_failure_task(
                task_id=task_id,
                context_id=self._resolve_context_id_from_params(params, task_id),
                operation=exc.operation,
            )
        except Exception:
            logger.exception("Agent execution failed")
            raise
        finally:
            if interrupted_or_non_blocking:
                cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
                cleanup_task.set_name(f"cleanup_producer:{task_id}")
                self._track_background_task(cleanup_task)
            else:
                try:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelled():
                        logger.debug(
                            "Client disconnected from message request. Cancelling task %s", task_id
                        )
                        producer_task.cancel()
                        await queue.close(immediate=True)

                    await asyncio.shield(self._cleanup_producer(producer_task, task_id))
                except asyncio.CancelledError:
                    pass

        if not result:
            raise InternalError()

        if hasattr(result, "id") and result.id:
            self._validate_task_id_match(task_id, result.id)
            if params.configuration and isinstance(result, Task):
                result = apply_history_length(result, params.configuration)

        await self._send_push_notification_if_needed(task_id, result_aggregator)

        return result


class IdentityAwareCallContextBuilder(DefaultServerCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        path = request.url.path
        raw_path = request.scope.get("raw_path")
        raw_value = ""
        if isinstance(raw_path, (bytes, bytearray)):
            raw_value = raw_path.decode(errors="ignore")
        is_stream = (
            path.endswith("/v1/message:stream")
            or path.endswith("/v1/message%3Astream")
            or raw_value.endswith("/v1/message:stream")
            or raw_value.endswith("/v1/message%3Astream")
        )
        if is_stream:
            context.state["a2a_streaming_request"] = True

        identity = getattr(request.state, "user_identity", None)
        if identity:
            context.state["identity"] = identity
        auth_scheme = getattr(request.state, "user_auth_scheme", None)
        if auth_scheme:
            context.state["auth_scheme"] = auth_scheme
        credential_id = getattr(request.state, "user_credential_id", None)
        if credential_id:
            context.state["credential_id"] = credential_id
        traceparent = getattr(request.state, "traceparent", None)
        if traceparent:
            context.state["traceparent"] = traceparent
        tracestate = getattr(request.state, "tracestate", None)
        if tracestate:
            context.state["tracestate"] = tracestate
        trace_id = getattr(request.state, "trace_id", None)
        if trace_id:
            context.state["trace_id"] = trace_id
        negotiated_protocol_version = getattr(request.state, "a2a_protocol_version", None)
        if negotiated_protocol_version:
            context.state["a2a_protocol_version"] = negotiated_protocol_version
        requested_protocol_version = getattr(request.state, "a2a_requested_protocol_version", None)
        if requested_protocol_version:
            context.state["a2a_requested_protocol_version"] = requested_protocol_version

        return context


def create_app(settings: Settings) -> FastAPI:
    install_log_record_factory()
    database_engine = (
        build_database_engine(settings) if settings.a2a_task_store_backend == "database" else None
    )
    session_state_repository = build_session_state_repository(settings, engine=database_engine)
    interrupt_request_repository = build_interrupt_request_repository(
        settings,
        engine=database_engine,
    )
    upstream_client = call_with_supported_kwargs(
        OpencodeUpstreamClient,
        settings,
        interrupt_request_repository=interrupt_request_repository,
    )
    client_manager = A2AClientManager(settings)
    agent_card = build_agent_card(settings)
    extended_agent_card = build_authenticated_extended_agent_card(settings)
    executor = call_with_supported_kwargs(
        OpencodeAgentExecutor,
        upstream_client,
        streaming_enabled=True,
        cancel_abort_timeout_seconds=settings.a2a_cancel_abort_timeout_seconds,
        pending_session_claim_ttl_seconds=settings.a2a_pending_session_claim_ttl_seconds,
        a2a_client_manager=client_manager,
        session_state_repository=session_state_repository,
    )
    task_store = call_with_supported_kwargs(
        build_task_store,
        settings,
        engine=database_engine,
    )
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
        extended_agent_card=extended_agent_card,
    )

    context_builder = IdentityAwareCallContextBuilder()
    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)

    jsonrpc_methods = {
        **capability_snapshot.session_management_methods(),
        **capability_snapshot.provider_discovery_methods(),
        **capability_snapshot.workspace_control_methods(),
        **capability_snapshot.interrupt_recovery_methods(),
        **capability_snapshot.interrupt_callback_methods(),
    }

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    jsonrpc_app = OpencodeSessionManagementJSONRPCApplication(
        http_handler=handler,
        context_builder=context_builder,
        upstream_client=upstream_client,
        protocol_version=settings.a2a_protocol_version,
        supported_methods=capability_snapshot.supported_jsonrpc_methods(),
        directory_resolver=(
            partial(
                executor._sandbox_policy.resolve_directory,
                default_directory=upstream_client.directory,
            )
            if hasattr(executor, "_sandbox_policy")
            else None
        ),
        session_claim=getattr(executor._session_manager, "claim_preferred_session", None),
        session_claim_finalize=getattr(executor._session_manager, "finalize_session_claim", None),
        session_claim_release=getattr(
            executor._session_manager,
            "release_preferred_session_claim",
            None,
        ),
        methods=jsonrpc_methods,
    )
    rest_dispatcher = RestDispatcher(
        request_handler=handler,
        context_builder=context_builder,
    )
    public_card_etag = build_agent_card_etag(agent_card)
    extended_card_etag = build_agent_card_etag(extended_agent_card)
    persistence_summary = describe_lightweight_persistence_backend(settings)
    lifespan = build_lifespan(
        database_engine=database_engine,
        task_store=task_store,
        session_state_repository=session_state_repository,
        interrupt_request_repository=interrupt_request_repository,
        client_manager=client_manager,
        upstream_client=upstream_client,
        persistence_summary=persistence_summary,
    )

    app = FastAPI(
        title=settings.a2a_title,
        version=settings.a2a_version,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=settings.a2a_http_gzip_minimum_size)
    jsonrpc_app.add_routes_to_app(app)

    async def public_agent_card_route() -> JSONResponse:
        return JSONResponse(agent_card_to_dict(agent_card))

    async def authenticated_extended_agent_card_route() -> JSONResponse:
        return JSONResponse(agent_card_to_dict(extended_agent_card))

    async def rest_message_send_route(request: Request) -> JSONResponse:
        try:

            async def _handler(context) -> SendMessageResponse:  # noqa: ANN001
                params = _parse_rest_send_message_request(await request.body())
                task_or_message = await handler.on_message_send(params, context)
                if isinstance(task_or_message, Task):
                    return SendMessageResponse(task=task_or_message)
                return SendMessageResponse(message=task_or_message)

            response = await rest_dispatcher._handle_non_streaming(request, _handler)
            return JSONResponse(content=MessageToDict(response))
        except Exception as error:  # noqa: BLE001
            return _rest_error_response(
                request=request,
                default_protocol_version=settings.a2a_protocol_version,
                error=error,
            )

    async def rest_message_send_stream_route(request: Request):
        try:

            async def _handler(context):  # noqa: ANN001
                params = _parse_rest_send_message_request(await request.body())
                async for event in handler.on_message_send_stream(params, context):
                    yield MessageToDict(proto_utils.to_stream_response(event))

            return await rest_dispatcher._handle_streaming(request, _handler)
        except Exception as error:  # noqa: BLE001
            return _rest_error_response(
                request=request,
                default_protocol_version=settings.a2a_protocol_version,
                error=error,
            )

    app.add_api_route(AGENT_CARD_WELL_KNOWN_PATH, public_agent_card_route, methods=["GET"])
    app.add_api_route(PREV_AGENT_CARD_WELL_KNOWN_PATH, public_agent_card_route, methods=["GET"])
    app.add_api_route("/v1/message:send", rest_message_send_route, methods=["POST"])
    app.add_api_route("/v1/message:stream", rest_message_send_stream_route, methods=["POST"])
    app.add_api_route("/v1/tasks/{id}:cancel", rest_dispatcher.on_cancel_task, methods=["POST"])
    app.add_api_route(
        "/v1/tasks/{id}:subscribe",
        rest_dispatcher.on_subscribe_to_task,
        methods=["GET"],
        operation_id="subscribe_to_task_get",
    )
    app.add_api_route(
        "/v1/tasks/{id}:subscribe",
        rest_dispatcher.on_subscribe_to_task,
        methods=["POST"],
        operation_id="subscribe_to_task_post",
    )
    app.add_api_route("/v1/tasks/{id}", rest_dispatcher.on_get_task, methods=["GET"])

    async def push_notifications_unsupported_route(request: Request) -> JSONResponse:
        protocol_version = getattr(
            request.state,
            "a2a_protocol_version",
            settings.a2a_protocol_version,
        )
        return JSONResponse(
            build_http_error_body(
                protocol_version=protocol_version,
                status_code=501,
                status="UNIMPLEMENTED",
                message=PUSH_NOTIFICATIONS_UNSUPPORTED_MESSAGE,
                legacy_payload={"message": PUSH_NOTIFICATIONS_UNSUPPORTED_MESSAGE},
                reason="PUSH_NOTIFICATIONS_UNSUPPORTED",
            ),
            status_code=501,
        )

    app.add_api_route(
        "/v1/tasks/{id}/pushNotificationConfigs/{push_id}",
        push_notifications_unsupported_route,
        methods=["GET"],
    )
    app.add_api_route(
        "/v1/tasks/{id}/pushNotificationConfigs/{push_id}",
        push_notifications_unsupported_route,
        methods=["DELETE"],
    )
    app.add_api_route(
        "/v1/tasks/{id}/pushNotificationConfigs",
        push_notifications_unsupported_route,
        methods=["POST"],
    )
    app.add_api_route(
        "/v1/tasks/{id}/pushNotificationConfigs",
        push_notifications_unsupported_route,
        methods=["GET"],
    )
    app.add_api_route(
        "/v1/tasks",
        build_list_tasks_route(
            task_store=task_store,
            default_protocol_version=settings.a2a_protocol_version,
        ),
        methods=["GET"],
    )
    app.add_api_route(
        EXTENDED_AGENT_CARD_PATH,
        authenticated_extended_agent_card_route,
        methods=["GET"],
    )
    app.state._jsonrpc_app = jsonrpc_app
    app.state.task_store = task_store
    app.state.persistence_summary = persistence_summary
    app.state.agent_executor = executor
    app.state.upstream_client = upstream_client
    app.state.a2a_client_manager = client_manager
    _patch_jsonrpc_openapi_contract(app, settings, runtime_profile=runtime_profile)
    install_runtime_middlewares(
        app,
        settings,
        public_card_etag=public_card_etag,
        extended_card_etag=extended_card_etag,
    )

    @app.get("/health")
    async def health_check():
        return runtime_profile.health_payload(
            service="opencode-a2a",
            version=settings.a2a_version,
            protocol_version=settings.a2a_protocol_version,
        )

    return app


def _normalize_log_level(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        return normalized
    return "WARNING"


def _configure_logging(level: str) -> None:
    install_log_record_factory()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s [trace_id=%(trace_id)s]: %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def main() -> None:
    settings_cls: type[BaseSettings] = Settings
    settings = cast(Settings, settings_cls())
    app = create_app(settings)
    log_level = _normalize_log_level(settings.a2a_log_level)
    _configure_logging(log_level)
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port, log_level=log_level.lower())


if __name__ == "__main__":
    main()

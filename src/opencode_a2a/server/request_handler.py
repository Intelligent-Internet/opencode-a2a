from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from typing import Any, cast

from a2a.server.agent_execution import (
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.cluster import VersionedTaskStore
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks.task_store import TaskStore
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import (
    InternalError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from google.protobuf.message import Message as ProtoMessage

from ..contracts.extensions import (
    MODEL_SELECTION_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
)
from ..extension_negotiation import (
    ExtensionRequirement,
    filter_negotiated_extensions_from_payload,
    requested_extensions_from_call_context,
)
from ..metadata_access import extract_namespaced_value
from ..output_modes import (
    annotate_output_negotiation_metadata,
    apply_accepted_output_modes,
    canonicalize_stream_artifacts,
    normalize_accepted_output_modes,
)
from ..task_states import TERMINAL_TASK_STATES
from .agent_card import _CHAT_OUTPUT_MODES
from .middleware import emit_stream_request_metrics
from .task_store import (
    NegotiatingTaskStore,
    NegotiatingVersionedTaskStore,
    TaskOutputModeRegistry,
    TaskStoreOperationError,
    task_store_get,
)

logger = logging.getLogger(__name__)

TASK_STORE_ERROR_TYPE = "TASK_STORE_UNAVAILABLE"


def _accepted_output_modes_from_params(params: Any) -> tuple[str, ...] | None:
    configuration = getattr(params, "configuration", None)
    return normalize_accepted_output_modes(configuration)


class OutputModeAwareRequestContextBuilder(RequestContextBuilder):
    """Record negotiated output modes before the SDK persists a new task."""

    def __init__(
        self,
        inner: RequestContextBuilder,
        output_modes: TaskOutputModeRegistry,
    ) -> None:
        self._inner = inner
        self._output_modes = output_modes

    async def build(
        self,
        context: ServerCallContext,
        params: SendMessageRequest | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
    ) -> RequestContext:
        request_context = await self._inner.build(
            context=context,
            params=params,
            task_id=task_id,
            context_id=context_id,
            task=task,
        )
        if params is not None and request_context.task_id:
            accepted = _accepted_output_modes_from_params(params)
            if accepted is not None:
                self._output_modes.remember(request_context.task_id, accepted)
        return request_context


class OpencodeRequestHandler(DefaultRequestHandlerV2):
    """Adapter contract on top of the SDK v2 active-task lifecycle."""

    def __init__(
        self,
        *,
        agent_executor: Any,
        task_store: TaskStore | VersionedTaskStore,
        agent_card: Any,
        event_stream: Any | None = None,
        output_modes: TaskOutputModeRegistry | None = None,
        **kwargs: Any,
    ) -> None:
        self._output_modes = output_modes or TaskOutputModeRegistry()
        if isinstance(task_store, VersionedTaskStore):
            negotiated_store: TaskStore | VersionedTaskStore = NegotiatingVersionedTaskStore(
                task_store,
                self._output_modes,
            )
        else:
            negotiated_store = NegotiatingTaskStore(task_store, self._output_modes)
        request_context_builder = kwargs.pop("request_context_builder", None)
        if request_context_builder is None:
            request_context_builder = SimpleRequestContextBuilder(
                should_populate_referred_tasks=False,
                task_store=None,
            )
        super().__init__(
            agent_executor=agent_executor,
            task_store=negotiated_store,
            agent_card=agent_card,
            event_stream=event_stream,
            request_context_builder=OutputModeAwareRequestContextBuilder(
                request_context_builder,
                self._output_modes,
            ),
            **kwargs,
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
    def _task_store_server_error(cls, exc: TaskStoreOperationError) -> InternalError:
        return InternalError(message=cls._task_store_failure_message(exc.operation))

    @staticmethod
    def _accepted_output_modes_from_params(params: Any) -> tuple[str, ...] | None:
        return _accepted_output_modes_from_params(params)

    def _modes_for_task(
        self,
        task_id: str,
        *,
        params: Any | None = None,
        task: Task | None = None,
    ) -> tuple[str, ...] | None:
        if params is not None:
            requested = self._accepted_output_modes_from_params(params)
            if requested is not None:
                return requested
        return self._output_modes.resolve(task_id, task)

    def _negotiate(self, payload: Any, accepted_output_modes: tuple[str, ...] | None) -> Any | None:
        negotiated = apply_accepted_output_modes(payload, accepted_output_modes)
        if negotiated is None:
            return None
        return annotate_output_negotiation_metadata(negotiated, accepted_output_modes)

    @staticmethod
    def _event_task_id(event: Event) -> str | None:
        if isinstance(event, Task):
            return event.id
        if isinstance(event, Message):
            return event.task_id or None
        return getattr(event, "task_id", None) or None

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        try:
            task = await super().on_get_task(params, context)
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc
        if task is None:
            return None
        modes = self._modes_for_task(params.id, task=task)
        negotiated = self._negotiate(task, modes)
        if not isinstance(negotiated, Task):
            return None
        filtered = filter_negotiated_extensions_from_payload(
            negotiated,
            requested_extensions_from_call_context(context),
        )
        return cast(Task, filtered)

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        try:
            page = await super().on_list_tasks(params, context)
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc
        for task in page.tasks:
            if not params.HasField("history_length") or params.history_length <= 0:
                task.ClearField("history")
            modes = self._modes_for_task(task.id, task=task)
            negotiated = self._negotiate(task, modes)
            if isinstance(negotiated, Task):
                task.CopyFrom(negotiated)
            filtered = filter_negotiated_extensions_from_payload(
                task,
                requested_extensions_from_call_context(context),
            )
            if isinstance(filtered, Task):
                task.CopyFrom(filtered)
        page.page_size = len(page.tasks)
        return cast(ListTasksResponse, page)

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        try:
            task = await task_store_get(self.task_store, params.id, context)
            if task is None:
                raise TaskNotFoundError
            if task.status.state == TaskState.TASK_STATE_CANCELED:
                modes = self._modes_for_task(task.id, task=task)
                return cast(Task, self._negotiate(task, modes))
            if task.status.state in TERMINAL_TASK_STATES:
                raise TaskNotCancelableError(
                    message=(
                        "Task cannot be canceled - current state: "
                        f"{TaskState.Name(task.status.state)}"
                    )
                )
            result = await super().on_cancel_task(params, context)
            if result is None:
                return None
            modes = self._modes_for_task(result.id, task=result)
            return cast(Task, self._negotiate(result, modes))
        except TaskNotCancelableError:
            refreshed = await task_store_get(self.task_store, params.id, context)
            if refreshed is not None and refreshed.status.state == TaskState.TASK_STATE_CANCELED:
                modes = self._modes_for_task(refreshed.id, task=refreshed)
                return cast(Task, self._negotiate(refreshed, modes))
            raise
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        try:
            task = await task_store_get(self.task_store, params.id, context)
            if task is None:
                raise TaskNotFoundError
            if task.status.state in TERMINAL_TASK_STATES:
                raise UnsupportedOperationError(
                    message="Cannot subscribe to a task in a terminal state"
                )
            modes = self._modes_for_task(params.id, task=task)
            async with aclosing(super().on_subscribe_to_task(params, context)) as subscription:
                async for event in subscription:
                    negotiated = self._negotiate(event, modes)
                    if negotiated is not None:
                        yield cast(Event, negotiated)
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc

    @classmethod
    def _validate_chat_output_modes(cls, params: Any) -> None:
        accepted_output_modes = cls._accepted_output_modes_from_params(params)
        if not accepted_output_modes:
            return
        if not (set(_CHAT_OUTPUT_MODES) & set(accepted_output_modes)):
            raise UnsupportedOperationError(
                message=(
                    "Requested acceptedOutputModes are not compatible with OpenCode chat responses."
                ),
                data={
                    "accepted_output_modes": list(accepted_output_modes),
                    "supported_output_modes": list(_CHAT_OUTPUT_MODES),
                },
            )
        if "text/plain" not in accepted_output_modes:
            raise UnsupportedOperationError(
                message="OpenCode chat responses require text/plain in acceptedOutputModes.",
                data={
                    "accepted_output_modes": list(accepted_output_modes),
                    "required_output_modes": ["text/plain"],
                    "supported_output_modes": list(_CHAT_OUTPUT_MODES),
                },
            )

    @staticmethod
    def _metadata_mapping(value: Any) -> dict[str, Any] | None:
        if isinstance(value, ProtoMessage):
            from google.protobuf.json_format import MessageToDict

            converted = MessageToDict(value, preserving_proto_field_name=True)
            return converted if isinstance(converted, dict) else None
        if isinstance(value, Mapping):
            return dict(value)
        return None

    @classmethod
    def _validate_shared_extension_negotiation(
        cls,
        params: Any,
        context: ServerCallContext | None = None,
    ) -> None:
        requested_extensions = requested_extensions_from_call_context(context)
        sources: list[dict[str, Any]] = []
        params_metadata = cls._metadata_mapping(getattr(params, "metadata", None))
        if params_metadata:
            sources.append(params_metadata)
        message = getattr(params, "message", None)
        message_metadata = cls._metadata_mapping(getattr(message, "metadata", None))
        if message_metadata:
            sources.append(message_metadata)

        requirements: list[ExtensionRequirement] = []
        if any(
            extract_namespaced_value(source, namespace="shared", path=("session", "id")) is not None
            for source in sources
        ):
            requirements.append(
                ExtensionRequirement(
                    extension_uri=SESSION_BINDING_EXTENSION_URI,
                    field="metadata.shared.session.id",
                )
            )
        if any(
            extract_namespaced_value(source, namespace="opencode", path=("directory",)) is not None
            for source in sources
        ):
            requirements.append(
                ExtensionRequirement(
                    extension_uri=SESSION_BINDING_EXTENSION_URI,
                    field="metadata.opencode.directory",
                )
            )
        if any(
            extract_namespaced_value(source, namespace="opencode", path=("workspace", "id"))
            is not None
            for source in sources
        ):
            requirements.append(
                ExtensionRequirement(
                    extension_uri=SESSION_BINDING_EXTENSION_URI,
                    field="metadata.opencode.workspace.id",
                )
            )
        if any(
            extract_namespaced_value(source, namespace="shared", path=("model", "providerID"))
            is not None
            or extract_namespaced_value(source, namespace="shared", path=("model", "modelID"))
            is not None
            for source in sources
        ):
            requirements.append(
                ExtensionRequirement(
                    extension_uri=MODEL_SELECTION_EXTENSION_URI,
                    field="metadata.shared.model",
                )
            )
        missing = [
            requirement
            for requirement in requirements
            if requirement.extension_uri not in requested_extensions
        ]
        if not missing:
            return
        raise UnsupportedOperationError(
            message="Request requires explicit A2A extension negotiation via A2A-Extensions.",
            data={
                "type": "EXTENSION_NEGOTIATION_REQUIRED",
                "fields": [requirement.field for requirement in missing],
                "required_extensions": sorted(
                    {requirement.extension_uri for requirement in missing}
                ),
                "requested_extensions": sorted(requested_extensions),
                "header": "A2A-Extensions",
            },
        )

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        self._validate_chat_output_modes(params)
        self._validate_shared_extension_negotiation(params, context)
        try:
            result = await super().on_message_send(params, context)
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc
        if isinstance(result, Task):
            result = canonicalize_stream_artifacts(result)
        task_id = getattr(result, "id", None) or params.message.task_id or ""
        modes = self._modes_for_task(
            task_id,
            params=params,
            task=result if isinstance(result, Task) else None,
        )
        negotiated = self._negotiate(result, modes)
        return cast(Task | Message, negotiated)

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        self._validate_chat_output_modes(params)
        self._validate_shared_extension_negotiation(params, context)
        emit_stream_request_metrics()
        emit_stream_request_metrics(active_delta=1.0)
        try:
            async with aclosing(super().on_message_send_stream(params, context)) as stream:
                async for event in stream:
                    task_id = self._event_task_id(event) or params.message.task_id or ""
                    modes = self._modes_for_task(
                        task_id,
                        params=params,
                        task=event if isinstance(event, Task) else None,
                    )
                    negotiated = self._negotiate(event, modes)
                    if negotiated is not None:
                        yield cast(Event, negotiated)
        except TaskStoreOperationError as exc:
            raise self._task_store_server_error(exc) from exc
        finally:
            emit_stream_request_metrics(active_delta=-1.0)

    @staticmethod
    def _task_error_metadata(operation: str) -> dict[str, Any]:
        return {
            "opencode": {
                "error": {
                    "type": TASK_STORE_ERROR_TYPE,
                    "operation": operation,
                }
            }
        }

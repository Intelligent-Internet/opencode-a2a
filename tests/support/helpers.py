from __future__ import annotations

from base64 import b64encode
from typing import Any
from unittest.mock import MagicMock, PropertyMock

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageConfiguration, SendMessageRequest

from opencode_a2a.config import Settings
from opencode_a2a.contracts.extensions import (
    MODEL_SELECTION_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
)
from opencode_a2a.opencode_upstream_client import OpencodeMessage
from opencode_a2a.server.context_helpers import normalize_server_call_context
from tests.support import settings as test_settings
from tests.support.interrupt_clients import InterruptRequestClientMixin


def make_basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


class DummyEventQueue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


def _default_requested_extensions() -> set[str]:
    return {
        MODEL_SELECTION_EXTENSION_URI,
        SESSION_BINDING_EXTENSION_URI,
        STREAMING_EXTENSION_URI,
    }


def _ensure_test_call_context(call_context: Any | None) -> Any:
    if call_context is None:
        return normalize_server_call_context(
            ServerCallContext(requested_extensions=_default_requested_extensions())
        )
    if not hasattr(call_context, "requested_extensions"):
        call_context.requested_extensions = _default_requested_extensions()
    return normalize_server_call_context(call_context)


def make_request_context_mock(
    *,
    task_id: str | None,
    context_id: str | None,
    identity: str | None = None,
    user_input: str = "",
    metadata: Any = None,
    message: Any = None,
    current_task: Any = None,
    call_context_enabled: bool = True,
) -> MagicMock:
    context = MagicMock(spec=RequestContext)
    context.task_id = task_id
    context.context_id = context_id
    context.get_user_input.return_value = user_input
    context.metadata = metadata
    context.message = message
    context.current_task = current_task
    if call_context_enabled:
        call_context = normalize_server_call_context(
            ServerCallContext(
                state={"identity": identity} if identity else {},
                requested_extensions=_default_requested_extensions(),
            )
        )
        context.call_context = call_context
    else:
        context.call_context = None
    return context


def configure_mock_client_runtime(
    client: Any,
    *,
    directory: str = "/tmp/workspace",
    settings_overrides: dict[str, Any] | None = None,
) -> None:
    overrides: dict[str, Any] = {
        "opencode_base_url": "http://localhost",
        "a2a_allow_directory_override": True,
    }
    if settings_overrides:
        overrides.update(settings_overrides)
    type(client).directory = PropertyMock(return_value=directory)
    type(client).settings = PropertyMock(return_value=test_settings.make_settings(**overrides))


def make_request_context(
    *,
    task_id: str,
    context_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    message_id: str = "req-1",
    accepted_output_modes: list[str] | None = None,
    call_context: Any = None,
) -> RequestContext:
    call_context = _ensure_test_call_context(call_context)
    message = Message(
        message_id=message_id,
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
    )
    configuration = (
        SendMessageConfiguration(accepted_output_modes=accepted_output_modes)
        if accepted_output_modes is not None
        else None
    )
    params = SendMessageRequest(message=message, metadata=metadata, configuration=configuration)
    return RequestContext(
        request=params,
        task_id=task_id,
        context_id=context_id,
        call_context=call_context,
    )


def make_request_context_with_parts(
    *,
    task_id: str,
    context_id: str,
    parts: list[Part],
    metadata: dict[str, Any] | None = None,
    message_id: str = "req-1",
    call_context: Any = None,
    accepted_output_modes: list[str] | None = None,
) -> RequestContext:
    call_context = _ensure_test_call_context(call_context)
    message = Message(
        message_id=message_id,
        role=Role.ROLE_USER,
        parts=parts,
    )
    configuration = (
        SendMessageConfiguration(accepted_output_modes=accepted_output_modes)
        if accepted_output_modes is not None
        else None
    )
    params = SendMessageRequest(message=message, metadata=metadata, configuration=configuration)
    return RequestContext(
        request=params,
        task_id=task_id,
        context_id=context_id,
        call_context=call_context,
    )


class DummyChatOpencodeUpstreamClient(InterruptRequestClientMixin):
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        interrupt_request_repository=None,  # noqa: ANN001
    ) -> None:
        del interrupt_request_repository
        self.created_sessions = 0
        self.sent_session_ids: list[str] = []
        self.sent_model_overrides: list[dict[str, str] | None] = []
        self.sent_workspace_ids: list[str | None] = []
        self.created_workspace_ids: list[str | None] = []
        self.stream_timeout = None
        self.directory = None
        self.settings = settings or test_settings.make_settings(
            opencode_base_url="http://localhost"
        )
        self._interrupt_requests: dict[str, dict[str, str | None]] = {}
        self._interrupt_request_details: dict[str, dict[str, Any] | None] = {}

    async def close(self) -> None:
        return None

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> str:
        del title, directory
        self.created_sessions += 1
        self.created_workspace_ids.append(workspace_id)
        return f"ses-created-{self.created_sessions}"

    async def send_message(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: list[dict[str, Any]] | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
        model_override: dict[str, str] | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del directory, timeout_override, parts
        self.sent_session_ids.append(session_id)
        self.sent_model_overrides.append(model_override)
        self.sent_workspace_ids.append(workspace_id)
        return OpencodeMessage(
            text=f"echo:{text or ''}",
            session_id=session_id,
            message_id="m-1",
            raw={},
        )

    async def stream_events(  # noqa: ANN001
        self,
        stop_event=None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        del stop_event, directory, workspace_id
        for _ in ():
            yield {}

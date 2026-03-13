from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, MessageSendParams, Role, TextPart

from opencode_a2a_server.config import Settings
from opencode_a2a_server.opencode_client import OpencodeMessage


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "opencode_base_url": "http://127.0.0.1:4096",
        "a2a_bearer_token": "test-token",
    }
    base.update(overrides)
    return Settings(**base)


class DummyEventQueue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


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
        call_context = MagicMock(spec=ServerCallContext)
        call_context.state = {"identity": identity} if identity else {}
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
        "a2a_bearer_token": "test",
        "opencode_base_url": "http://localhost",
        "a2a_allow_directory_override": True,
    }
    if settings_overrides:
        overrides.update(settings_overrides)
    type(client).directory = PropertyMock(return_value=directory)
    type(client).settings = PropertyMock(return_value=make_settings(**overrides))


def make_request_context(
    *,
    task_id: str,
    context_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    message_id: str = "req-1",
) -> RequestContext:
    message = Message(
        message_id=message_id,
        role=Role.user,
        parts=[TextPart(text=text)],
    )
    params = MessageSendParams(message=message, metadata=metadata)
    return RequestContext(request=params, task_id=task_id, context_id=context_id)


class DummyChatOpencodeClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.created_sessions = 0
        self.sent_session_ids: list[str] = []
        self.sent_model_overrides: list[dict[str, str] | None] = []
        self.stream_timeout = None
        self.directory = None
        self.settings = settings or make_settings(
            a2a_bearer_token="test",
            opencode_base_url="http://localhost",
        )

    async def close(self) -> None:
        return None

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del title, directory
        self.created_sessions += 1
        return f"ses-created-{self.created_sessions}"

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        model_override: dict[str, str] | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        del directory, timeout_override
        self.sent_session_ids.append(session_id)
        self.sent_model_overrides.append(model_override)
        return OpencodeMessage(
            text=f"echo:{text}",
            session_id=session_id,
            message_id="m-1",
            raw={},
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del stop_event, directory
        for _ in ():
            yield {}

    def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str | None = None,
        identity: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        del request_id, session_id, interrupt_type, identity, task_id, context_id, ttl_seconds

    def resolve_interrupt_session(self, request_id: str) -> str | None:
        del request_id
        return None

    def discard_interrupt_request(self, request_id: str) -> None:
        del request_id


class DummySessionQueryOpencodeClient:
    def __init__(self, _settings: Settings) -> None:
        self.settings = _settings
        self.directory = _settings.opencode_directory
        self._sessions_payload = [{"id": "s-1", "title": "Session s-1"}]
        self._messages_payload = [
            {
                "info": {"id": "m-1", "role": "assistant"},
                "parts": [{"type": "text", "text": "SECRET_HISTORY"}],
            }
        ]
        self.last_sessions_params = None
        self.last_messages_params = None
        self.prompt_async_calls: list[dict[str, Any]] = []
        self.command_calls: list[dict[str, Any]] = []
        self.shell_calls: list[dict[str, Any]] = []
        self.provider_catalog_payload: dict[str, Any] = {
            "all": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "source": "api",
                    "models": {
                        "gpt-5": {
                            "name": "GPT-5",
                            "status": "active",
                            "limit": {"context": 200000, "output": 8192},
                            "capabilities": {
                                "reasoning": True,
                                "toolcall": True,
                                "attachment": False,
                            },
                        }
                    },
                },
                {
                    "id": "google",
                    "name": "Google",
                    "source": "config",
                    "models": {
                        "gemini-2.5-flash": {
                            "name": "Gemini 2.5 Flash",
                            "status": "beta",
                            "limit": {"context": 1000000, "output": 8192},
                            "capabilities": {
                                "reasoning": True,
                                "toolcall": True,
                                "attachment": True,
                            },
                        }
                    },
                },
            ],
            "default": {
                "openai": "gpt-5",
                "google": "gemini-2.5-flash",
            },
            "connected": ["openai"],
        }
        self._interrupt_requests: dict[str, dict[str, str | None]] = {}

    async def close(self) -> None:
        return None

    async def list_sessions(self, *, params=None):
        self.last_sessions_params = params
        return self._sessions_payload

    async def list_messages(self, session_id: str, *, params=None):
        assert session_id
        self.last_messages_params = params
        return self._messages_payload

    async def session_prompt_async(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> None:
        self.prompt_async_calls.append(
            {
                "session_id": session_id,
                "request": request,
                "directory": directory,
            }
        )

    async def session_command(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        self.command_calls.append(
            {
                "session_id": session_id,
                "request": request,
                "directory": directory,
            }
        )
        return {
            "info": {"id": "msg-command-1", "role": "assistant"},
            "parts": [{"type": "text", "text": "Command completed."}],
        }

    async def session_shell(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> dict[str, Any]:
        self.shell_calls.append(
            {
                "session_id": session_id,
                "request": request,
                "directory": directory,
            }
        )
        return {
            "id": "msg-shell-1",
            "role": "assistant",
            "parts": [{"type": "text", "text": "Shell command executed."}],
        }

    async def list_provider_catalog(self, *, directory: str | None = None):
        del directory
        return self.provider_catalog_payload

    def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        del ttl_seconds
        self._interrupt_requests[request_id] = {
            "session_id": session_id,
            "interrupt_type": interrupt_type,
            "identity": identity,
            "task_id": task_id,
            "context_id": context_id,
        }

    def resolve_interrupt_request(self, request_id: str):
        payload = self._interrupt_requests.get(request_id)
        if payload is None:
            return "missing", None

        class _Binding:
            def __init__(self, data: dict[str, str | None]) -> None:
                self.session_id = data.get("session_id")
                self.interrupt_type = data.get("interrupt_type")
                self.identity = data.get("identity")
                self.task_id = data.get("task_id")
                self.context_id = data.get("context_id")

        return "active", _Binding(payload)

    def resolve_interrupt_session(self, request_id: str) -> str | None:
        payload = self._interrupt_requests.get(request_id)
        if payload is None:
            return None
        return payload.get("session_id")

    def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_requests.pop(request_id, None)

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
    ) -> bool:
        del request_id, reply, message, directory
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
    ) -> bool:
        del request_id, answers, directory
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
    ) -> bool:
        del request_id, directory
        return True

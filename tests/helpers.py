from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock

from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext

from opencode_a2a_serve.config import Settings


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

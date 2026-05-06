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
from opencode_a2a.opencode_upstream_client import OpencodeMessage, OpencodeMessagePage
from opencode_a2a.server.context_helpers import normalize_server_call_context
from tests.support import settings as test_settings


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


class DummyChatOpencodeUpstreamClient:
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

    async def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str | None = None,
        identity: str | None = None,
        credential_id: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        details: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        del (
            request_id,
            session_id,
            interrupt_type,
            identity,
            credential_id,
            task_id,
            context_id,
            details,
            ttl_seconds,
        )

    async def resolve_interrupt_session(self, request_id: str) -> str | None:
        del request_id
        return None

    async def discard_interrupt_request(self, request_id: str) -> None:
        del request_id


class DummySessionQueryOpencodeUpstreamClient:
    def __init__(
        self,
        _settings: Settings,
        *,
        interrupt_request_repository=None,  # noqa: ANN001
    ) -> None:
        del interrupt_request_repository
        self.settings = _settings
        self.directory = _settings.opencode_workspace_root
        self._sessions_payload = [{"id": "s-1", "title": "Session s-1"}]
        self._session_status_payload = {
            "s-1": {"type": "idle"},
            "s-2": {"type": "retry", "attempt": 2, "message": "retrying", "next": 30},
        }
        self._session_payload = {
            "id": "s-1",
            "title": "Session s-1",
            "directory": "/workspace",
            "projectID": "proj-1",
        }
        self._child_sessions_payload = [{"id": "s-2", "title": "Child session"}]
        self._todo_payload = [
            {
                "id": "todo-1",
                "content": "Review the diff",
                "status": "pending",
                "priority": "high",
            }
        ]
        self._diff_payload = [
            {
                "file": "src/app.py",
                "before": "old",
                "after": "new",
                "additions": 3,
                "deletions": 1,
            }
        ]
        self._messages_payload = [
            {
                "info": {"id": "m-1", "role": "assistant"},
                "parts": [{"type": "text", "text": "SECRET_HISTORY"}],
            }
        ]
        self._message_payload = {
            "info": {"id": "m-1", "role": "assistant"},
            "parts": [{"type": "text", "text": "One message payload"}],
        }
        self._reverted_session_payload = {
            "id": "s-1",
            "title": "Reverted session",
            "directory": "/workspace",
            "projectID": "proj-1",
            "revert": {
                "messageID": "msg-1",
                "partID": "part-1",
                "snapshot": "snap-1",
                "diff": "diff-1",
            },
        }
        self._unreverted_session_payload = {
            "id": "s-1",
            "title": "Restored session",
            "directory": "/workspace",
            "projectID": "proj-1",
        }
        self._messages_next_cursor: str | None = None
        self.last_sessions_params = None
        self.last_sessions_directory: str | None = None
        self.last_sessions_workspace_id: str | None = None
        self.last_messages_params = None
        self.last_messages_workspace_id: str | None = None
        self.lifecycle_calls: list[dict[str, Any]] = []
        self.prompt_async_calls: list[dict[str, Any]] = []
        self.command_calls: list[dict[str, Any]] = []
        self.shell_calls: list[dict[str, Any]] = []
        self.workspace_control_calls: list[dict[str, Any]] = []
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
        self._interrupt_request_details: dict[str, dict[str, Any] | None] = {}

    async def close(self) -> None:
        return None

    async def list_sessions(
        self,
        *,
        params=None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.last_sessions_directory = directory
        self.last_sessions_workspace_id = workspace_id
        self.last_sessions_params = params
        return self._sessions_payload

    async def list_messages(self, session_id: str, *, params=None, workspace_id: str | None = None):
        assert session_id
        self.last_messages_params = params
        self.last_messages_workspace_id = workspace_id
        return OpencodeMessagePage(
            payload=self._messages_payload,
            next_cursor=self._messages_next_cursor,
        )

    async def session_status(
        self,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "session_status",
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._session_status_payload

    async def get_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "get_session",
                "session_id": session_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._session_payload

    async def list_child_sessions(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "list_child_sessions",
                "session_id": session_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._child_sessions_payload

    async def get_session_todo(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "get_session_todo",
                "session_id": session_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._todo_payload

    async def get_session_diff(
        self,
        session_id: str,
        *,
        params=None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "get_session_diff",
                "session_id": session_id,
                "params": params,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._diff_payload

    async def get_message(
        self,
        session_id: str,
        message_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "get_message",
                "session_id": session_id,
                "message_id": message_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._message_payload

    async def session_prompt_async(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self.prompt_async_calls.append(
            {
                "session_id": session_id,
                "request": request,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )

    async def session_command(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        self.command_calls.append(
            {
                "session_id": session_id,
                "request": request,
                "directory": directory,
                "workspace_id": workspace_id,
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
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        self.shell_calls.append(
            {
                "session_id": session_id,
                "request": request,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return {
            "id": "msg-shell-1",
            "role": "assistant",
            "parts": [{"type": "text", "text": "Shell command executed."}],
        }

    async def fork_session(
        self,
        session_id: str,
        request: dict[str, Any] | None = None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "fork_session",
                "session_id": session_id,
                "request": request,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return {
            "id": "s-2",
            "title": "Forked session",
            "parentID": session_id,
            "directory": "/workspace",
            "projectID": "proj-1",
        }

    async def share_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "share_session",
                "session_id": session_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return {
            "id": session_id,
            "title": "Shared session",
            "directory": "/workspace",
            "projectID": "proj-1",
            "share": {"url": "https://example.com/shared/s-1"},
        }

    async def unshare_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "unshare_session",
                "session_id": session_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return {
            "id": session_id,
            "title": "Unshared session",
            "directory": "/workspace",
            "projectID": "proj-1",
        }

    async def summarize_session(
        self,
        session_id: str,
        request: dict[str, Any] | None = None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "summarize_session",
                "session_id": session_id,
                "request": request,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return True

    async def revert_session(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "revert_session",
                "session_id": session_id,
                "request": request,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._reverted_session_payload

    async def unrevert_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.lifecycle_calls.append(
            {
                "method": "unrevert_session",
                "session_id": session_id,
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self._unreverted_session_payload

    async def list_provider_catalog(
        self,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.workspace_control_calls.append(
            {
                "method": "provider_catalog",
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self.provider_catalog_payload

    async def list_projects(self):
        self.workspace_control_calls.append({"method": "list_projects"})
        return [{"id": "proj-1", "name": "Alpha", "directory": "/workspace"}]

    async def get_current_project(self):
        self.workspace_control_calls.append({"method": "get_current_project"})
        return {"id": "proj-1", "name": "Alpha", "directory": "/workspace"}

    async def list_workspaces(self):
        self.workspace_control_calls.append({"method": "list_workspaces"})
        return [{"id": "wrk-1", "type": "git", "branch": "main", "directory": None}]

    async def create_workspace(self, request: dict[str, Any]):
        self.workspace_control_calls.append({"method": "create_workspace", "request": request})
        return {"id": "wrk-2", **request}

    async def remove_workspace(self, workspace_id: str):
        self.workspace_control_calls.append(
            {"method": "remove_workspace", "workspace_id": workspace_id}
        )
        return {"id": workspace_id, "type": "git", "branch": "main", "directory": None}

    async def list_worktrees(self):
        self.workspace_control_calls.append({"method": "list_worktrees"})
        return ["/tmp/worktrees/alpha"]

    async def create_worktree(self, request: dict[str, Any]):
        self.workspace_control_calls.append({"method": "create_worktree", "request": request})
        return {
            "name": request.get("name") or "feature-branch",
            "branch": "opencode/feature-branch",
            "directory": "/tmp/worktrees/feature-branch",
        }

    async def remove_worktree(self, request: dict[str, Any]) -> bool:
        self.workspace_control_calls.append({"method": "remove_worktree", "request": request})
        return True

    async def reset_worktree(self, request: dict[str, Any]) -> bool:
        self.workspace_control_calls.append({"method": "reset_worktree", "request": request})
        return True

    async def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None = None,
        credential_id: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        details: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        del ttl_seconds
        self._interrupt_requests[request_id] = {
            "session_id": session_id,
            "interrupt_type": interrupt_type,
            "identity": identity,
            "credential_id": credential_id,
            "task_id": task_id,
            "context_id": context_id,
        }
        self._interrupt_request_details[request_id] = (
            dict(details) if isinstance(details, dict) else None
        )

    async def resolve_interrupt_request(self, request_id: str):
        payload = self._interrupt_requests.get(request_id)
        if payload is None:
            return "missing", None

        class _Binding:
            def __init__(self, data: dict[str, str | None]) -> None:
                self.request_id = request_id
                self.session_id = data.get("session_id")
                self.interrupt_type = data.get("interrupt_type")
                self.identity = data.get("identity")
                self.credential_id = data.get("credential_id")
                self.task_id = data.get("task_id")
                self.context_id = data.get("context_id")
                self.details = self_details

        self_details = self._interrupt_request_details.get(request_id)

        return "active", _Binding(payload)

    async def resolve_interrupt_session(self, request_id: str) -> str | None:
        payload = self._interrupt_requests.get(request_id)
        if payload is None:
            return None
        return payload.get("session_id")

    async def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_requests.pop(request_id, None)
        self._interrupt_request_details.pop(request_id, None)

    async def list_interrupt_requests(
        self,
        *,
        identity: str,
        interrupt_type: str | None = None,
    ):
        class _Binding:
            def __init__(
                self,
                *,
                request_id: str,
                data: dict[str, str | None],
                details: dict[str, Any] | None,
            ) -> None:
                self.request_id = request_id
                self.session_id = data.get("session_id")
                self.interrupt_type = data.get("interrupt_type")
                self.identity = data.get("identity")
                self.credential_id = data.get("credential_id")
                self.task_id = data.get("task_id")
                self.context_id = data.get("context_id")
                self.details = details
                self.expires_at = 0.0

        items = []
        for request_id, payload in self._interrupt_requests.items():
            if payload.get("identity") != identity:
                continue
            if interrupt_type is not None and payload.get("interrupt_type") != interrupt_type:
                continue
            items.append(
                _Binding(
                    request_id=request_id,
                    data=payload,
                    details=self._interrupt_request_details.get(request_id),
                )
            )
        return items

    async def list_permission_requests(self, *, identity: str):
        return await self.list_interrupt_requests(identity=identity, interrupt_type="permission")

    async def list_question_requests(self, *, identity: str):
        return await self.list_interrupt_requests(identity=identity, interrupt_type="question")

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        del request_id, reply, message, directory, workspace_id
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        del request_id, answers, directory, workspace_id
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        del request_id, directory, workspace_id
        return True

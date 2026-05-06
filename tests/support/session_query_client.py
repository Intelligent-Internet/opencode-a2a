from __future__ import annotations

from typing import Any

from opencode_a2a.config import Settings
from opencode_a2a.opencode_upstream_client import OpencodeMessagePage
from tests.support.interrupt_clients import InterruptRequestClientMixin
from tests.support.workspace_control_client import WorkspaceControlClientMixin


class DummySessionQueryOpencodeUpstreamClient(
    WorkspaceControlClientMixin,
    InterruptRequestClientMixin,
):
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

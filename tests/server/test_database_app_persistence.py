from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from a2a.types import Task, TaskState, TaskStatus

from opencode_a2a.opencode_upstream_client import OpencodeMessage
from tests.support.helpers import make_settings


def _task(task_id: str, *, context_id: str = "ctx-1") -> Task:
    return Task(
        id=task_id,
        contextId=context_id,
        status=TaskStatus(state=TaskState.working),
    )


def _task_store_from_app(app):  # noqa: ANN001
    return app.state.task_store


def _executor_from_app(app):  # noqa: ANN001
    return app.state.agent_executor


@pytest.mark.asyncio
async def test_database_backend_persists_task_session_and_interrupt_state_across_app_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import opencode_a2a.server.application as app_module

    class PersistentStateDummyClient:
        created_sessions = 0
        permission_reply_calls: list[dict[str, str | None]] = []

        def __init__(self, settings, *, interrupt_request_repository=None) -> None:  # noqa: ANN001
            self.settings = settings
            self.directory = settings.opencode_workspace_root
            self.stream_timeout = None
            self._interrupt_request_repository = interrupt_request_repository

        async def close(self) -> None:
            return None

        async def create_session(
            self,
            title: str | None = None,
            *,
            directory: str | None = None,
        ) -> str:
            del title, directory
            type(self).created_sessions += 1
            return f"ses-{type(self).created_sessions}"

        async def send_message(
            self,
            session_id: str,
            text: str | None = None,
            *,
            parts=None,  # noqa: ANN001
            directory: str | None = None,
            model_override=None,  # noqa: ANN001
            timeout_override=None,  # noqa: ANN001
        ) -> OpencodeMessage:
            del text, parts, directory, model_override, timeout_override
            return OpencodeMessage(
                text="ok",
                session_id=session_id,
                message_id="m-1",
                raw={},
            )

        async def remember_interrupt_request(
            self,
            *,
            request_id: str,
            session_id: str,
            interrupt_type: str,
            identity: str | None = None,
            task_id: str | None = None,
            context_id: str | None = None,
            details: dict | None = None,
            ttl_seconds: float | None = None,
        ) -> None:
            assert self._interrupt_request_repository is not None
            await self._interrupt_request_repository.remember(
                request_id=request_id,
                session_id=session_id,
                interrupt_type=interrupt_type,
                identity=identity,
                task_id=task_id,
                context_id=context_id,
                details=details,
                ttl_seconds=ttl_seconds,
            )

        async def resolve_interrupt_request(self, request_id: str):
            assert self._interrupt_request_repository is not None
            return await self._interrupt_request_repository.resolve(request_id=request_id)

        async def resolve_interrupt_session(self, request_id: str) -> str | None:
            status, binding = await self.resolve_interrupt_request(request_id)
            if status != "active" or binding is None:
                return None
            return binding.session_id

        async def list_permission_requests(self, *, identity: str):
            assert self._interrupt_request_repository is not None
            return await self._interrupt_request_repository.list_pending(
                identity=identity,
                interrupt_type="permission",
            )

        async def list_question_requests(self, *, identity: str):
            assert self._interrupt_request_repository is not None
            return await self._interrupt_request_repository.list_pending(
                identity=identity,
                interrupt_type="question",
            )

        async def discard_interrupt_request(self, request_id: str) -> None:
            assert self._interrupt_request_repository is not None
            await self._interrupt_request_repository.discard(request_id=request_id)

        async def permission_reply(
            self,
            request_id: str,
            *,
            reply: str,
            message: str | None = None,
            directory: str | None = None,
        ) -> bool:
            type(self).permission_reply_calls.append(
                {
                    "request_id": request_id,
                    "reply": reply,
                    "message": message,
                    "directory": directory,
                }
            )
            return True

    PersistentStateDummyClient.created_sessions = 0
    PersistentStateDummyClient.permission_reply_calls = []
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", PersistentStateDummyClient)

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'app-state.db'}"
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_task_store_database_url=database_url,
    )

    app1 = app_module.create_app(settings)
    async with app1.router.lifespan_context(app1):
        task_store = _task_store_from_app(app1)
        executor = _executor_from_app(app1)
        upstream_client = app1.state._jsonrpc_app._upstream_client

        await task_store.save(_task("task-1"))
        session_id, pending = await executor._session_manager.get_or_create_session(
            identity="user-1",
            context_id="ctx-1",
            title="hello",
        )
        assert pending is False
        assert session_id == "ses-1"
        await upstream_client.remember_interrupt_request(
            request_id="perm-1",
            session_id=session_id,
            interrupt_type="permission",
            identity=f"bearer:{hashlib.sha256(b'test-token').hexdigest()[:12]}",
            task_id="task-1",
            context_id="ctx-1",
            details={"permission": "read", "patterns": ["/tmp/config.yml"]},
            ttl_seconds=60.0,
        )

    app2 = app_module.create_app(settings)
    async with app2.router.lifespan_context(app2):
        task_store = _task_store_from_app(app2)
        executor = _executor_from_app(app2)

        restored_task = await task_store.get("task-1")
        assert restored_task is not None
        assert restored_task.id == "task-1"

        restored_session_id, pending = await executor._session_manager.get_or_create_session(
            identity="user-1",
            context_id="ctx-1",
            title="hello again",
        )
        assert pending is False
        assert restored_session_id == "ses-1"
        assert PersistentStateDummyClient.created_sessions == 1

        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            query_response = await client.post(
                "/",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "opencode.permissions.list",
                    "params": {},
                },
            )
            response = await client.post(
                "/",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "a2a.interrupt.permission.reply",
                    "params": {
                        "request_id": "perm-1",
                        "reply": "once",
                    },
                },
            )

        query_payload = query_response.json()
        assert query_payload["result"]["items"] == [
            {
                "request_id": "perm-1",
                "session_id": "ses-1",
                "interrupt_type": "permission",
                "task_id": "task-1",
                "context_id": "ctx-1",
                "details": {"permission": "read", "patterns": ["/tmp/config.yml"]},
                "expires_at": query_payload["result"]["items"][0]["expires_at"],
            }
        ]

        payload = response.json()
        assert payload.get("error") is None
        assert payload["result"]["ok"] is True
        assert payload["result"]["request_id"] == "perm-1"
        assert PersistentStateDummyClient.permission_reply_calls == [
            {
                "request_id": "perm-1",
                "reply": "once",
                "message": None,
                "directory": None,
            }
        ]

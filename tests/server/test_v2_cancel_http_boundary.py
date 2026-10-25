from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from opencode_a2a.server.application import create_app
from tests.support.settings import make_settings
from tests.upstream.test_v2_execution import EventStream, NativeServer


class HttpBoundary(NativeServer):
    """Real host/SDK lifecycle; only the native HTTP server is simulated."""

    def __init__(self, tmp_path: Path) -> None:
        self.app = create_app(
            make_settings(
                opencode_api_version="v2",
                opencode_v2_execution="exclusive",
                opencode_workspace_root="/workspace",
                opencode_auth_password="synthetic-test-password",  # pragma: allowlist secret
                a2a_task_store_backend="database",
                a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}",
                a2a_cancel_abort_timeout_seconds=0.1,
            )
        )
        self.client = self.app.state.upstream_client
        self.engine = self.client._v2_execution
        self.engine.timeout = 3
        self.mode = "waiting"
        self.cancel_mode = "interrupt"
        self.feed = EventStream()
        self.feed.send({"type": "server.connected"})
        self.requests: list[httpx.Request] = []
        self.messages: dict[str, list[dict[str, object]]] = {}
        self.inputs: list[str] = []
        self.sequences: dict[str, int] = {}
        self.admitted = asyncio.Event()
        self.settling = asyncio.Event()
        self.settle_release = asyncio.Event()
        self.pending: list[asyncio.Task[httpx.Response]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/session/ses_one" and request.method == "GET":
            from opencode_a2a.upstream.v2_profile import exclusive_permissions

            self.requests.append(request)
            return httpx.Response(200, json={"data": {"permissions": exclusive_permissions()}})
        if path.endswith("/wait") and self.cancel_mode == "settling":
            self.settling.set()
            await self.settle_release.wait()
            return httpx.Response(204)
        if path in {"/api/info", "/api/config", "/api/plugin", "/api/session"}:
            self.requests.append(request)
            return httpx.Response(
                200,
                json={
                    "/api/info": {"version": "2.0.28"},
                    "/api/config": [],
                    "/api/plugin": {"data": []},
                    "/api/session": {"data": {"id": "ses_one"}},
                }[path],
            )
        if path.endswith("/interrupt") and self.cancel_mode != "interrupt":
            self.requests.append(request)
            if self.cancel_mode == "timeout":
                await asyncio.Event().wait()
            if self.cancel_mode == "http-error":
                return httpx.Response(503)
            self.execution("ses_one", self.inputs[0])
            return httpx.Response(200, json={"interrupted": False})
        return await super().handle(request)

    async def start(self, http: httpx.AsyncClient) -> tuple[str, asyncio.Task[httpx.Response]]:
        executor = self.app.state.agent_executor
        previous = set(executor._running_requests)
        stream = asyncio.create_task(
            http.post(
                "/message:stream",
                json={
                    "message": {
                        "messageId": f"input-{len(self.pending)}",
                        "contextId": "conversation",
                        "role": "ROLE_USER",
                        "parts": [{"text": "wait for cancellation"}],
                    }
                },
            )
        )
        self.pending.append(stream)
        # Real SQLite startup is not the cancellation deadline under test.
        async with asyncio.timeout(10):
            while True:
                keys = set(executor._running_requests) - previous
                if keys:
                    task_id = next(iter(keys))[0]
                    response = await http.get(f"/tasks/{task_id}")
                    if response.status_code == 200:
                        return task_id, stream
                if stream.done():
                    raise AssertionError(f"Stream ended before execution: {(await stream).text}")
                await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[tuple[HttpBoundary, httpx.AsyncClient]]:
    server = HttpBoundary(tmp_path)
    monkeypatch.setattr(server.client._client, "_transport", httpx.MockTransport(server.handle))
    async with server.app.router.lifespan_context(server.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token", "A2A-Version": "1.0"},
        ) as http:
            try:
                yield server, http
            finally:
                for stream in server.pending:
                    stream.cancel()
                await asyncio.gather(*server.pending, return_exceptions=True)
    assert server.feed.closed


def stream_states(response: httpx.Response) -> list[str]:
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    return [event["statusUpdate"]["status"]["state"] for event in events if "statusUpdate" in event]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("interrupt", "TASK_STATE_CANCELED"),
        ("refused", "TASK_STATE_COMPLETED"),
        ("settling", "TASK_STATE_COMPLETED"),
        ("http-error", "TASK_STATE_FAILED"),
        ("timeout", "TASK_STATE_FAILED"),
    ],
)
async def test_cancel_preserves_observed_outcome_through_real_sdk(boundary, mode, expected) -> None:
    server, http = boundary
    server.cancel_mode = mode
    task_id, stream = await server.start(http)
    await asyncio.wait_for(server.admitted.wait(), 2)
    if mode == "settling":
        server.execution("ses_one", server.inputs[0])
        await asyncio.wait_for(server.settling.wait(), 2)
    cancel = asyncio.create_task(http.post(f"/tasks/{task_id}:cancel", json={}))
    server.pending.append(cancel)
    if mode == "settling":
        async with asyncio.timeout(2):
            executor = server.app.state.agent_executor
            while (task_id, "conversation") not in executor._cancelled_requests:
                await asyncio.sleep(0)
            server.settle_release.set()
    canceled = await asyncio.wait_for(cancel, 2)
    finished = await asyncio.wait_for(stream, 2)
    stored = await http.get(f"/tasks/{task_id}")
    assert stored.status_code == 200 and stored.json()["status"]["state"] == expected
    assert finished.status_code == 200 and stream_states(finished)[-1] == expected
    assert "TASK_STATE_CANCELED" not in stream_states(finished) or mode == "interrupt"
    if mode == "interrupt":
        assert canceled.status_code == 200 and canceled.json()["status"]["state"] == expected
    else:
        assert canceled.status_code == 400
        assert canceled.json()["error"]["details"][0]["reason"] == "TASK_NOT_CANCELABLE"
    if mode in {"refused", "settling"}:
        assert "TASK_STATE_FAILED" not in stream_states(finished)
        assert server.engine.sessions["ses_one"].subscription.error is None
        assert "ses_one" in json.dumps(stored.json()["artifacts"])
    if mode in {"http-error", "timeout"}:
        assert server.engine.sessions["ses_one"].subscription.error is not None
        assert server.engine._active == 1


@pytest.mark.asyncio
async def test_queued_cancel_closes_original_stream_without_sdk_lock_deadlock(boundary) -> None:
    server, http = boundary
    active_id, active = await server.start(http)
    await asyncio.wait_for(server.admitted.wait(), 2)
    queued_id, queued = await server.start(http)
    canceled = await asyncio.wait_for(http.post(f"/tasks/{queued_id}:cancel", json={}), 2)
    finished = await asyncio.wait_for(queued, 2)
    assert canceled.status_code == 200
    assert stream_states(finished)[-1] == "TASK_STATE_CANCELED"
    assert not active.done() and len(server.inputs) == 1
    assert not any(request.url.path.endswith("/interrupt") for request in server.requests)
    lock = await server.app.state.agent_executor._session_manager.get_session_lock("ses_one")
    assert lock.locked()
    server.execution("ses_one", server.inputs[0])
    completed = await asyncio.wait_for(active, 2)
    stored = await http.get(f"/tasks/{active_id}")
    assert stream_states(completed)[-1] == "TASK_STATE_COMPLETED"
    assert stored.json()["status"]["state"] == "TASK_STATE_COMPLETED"
    assert not lock.locked() and server.engine._active == 0

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Task, TaskState, TaskStatusUpdateEvent
from a2a.utils.errors import TaskNotCancelableError

from opencode_a2a.execution.executor import OpencodeAgentExecutor
from opencode_a2a.upstream.v2 import V2UpstreamAdapter
from opencode_a2a.upstream.v2_events import V2ExecutionUncertainError
from tests.support.helpers import DummyEventQueue, make_request_context
from tests.support.settings import make_settings
from tests.upstream.test_v2_execution import EventStream, NativeServer


class ExecutorServer(NativeServer):
    """Native HTTP boundary with the actual adapter, executor and session manager."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mode = "waiting"
        self.cancel_mode = "interrupt"
        self.feed = EventStream()
        self.feed.send({"type": "server.connected"})
        self.requests: list[httpx.Request] = []
        self.messages: dict[str, list[dict[str, object]]] = {}
        self.inputs: list[str] = []
        self.sequences: dict[str, int] = {}
        self.admitted = asyncio.Event()
        self.interrupt_entered = asyncio.Event()
        self.interrupt_release = asyncio.Event()
        self.client = V2UpstreamAdapter(
            make_settings(
                opencode_api_version="v2",
                opencode_v2_execution="exclusive",
                opencode_workspace_root="/workspace",
                opencode_auth_password="synthetic-test-password",  # pragma: allowlist secret
            )
        )
        monkeypatch.setattr(self.client._client, "_transport", httpx.MockTransport(self.handle))
        self.hub = self.client._v2_hub
        self.engine = self.client._v2_execution
        self.engine.timeout = 1
        self.executor = OpencodeAgentExecutor(self.client, streaming_enabled=False)
        self.tasks: list[asyncio.Task[None]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/session/ses_one" and request.method == "GET":
            from opencode_a2a.upstream.v2_profile import exclusive_permissions

            self.requests.append(request)
            return httpx.Response(200, json={"data": {"permissions": exclusive_permissions()}})
        if path in {"/api/info", "/api/config", "/api/plugin", "/api/session"}:
            self.requests.append(request)
            payload: object = {
                "/api/info": {"version": "2.0.28"},
                "/api/config": [],
                "/api/plugin": {"data": []},
                "/api/session": {"data": {"id": "ses_one"}},
            }[path]
            return httpx.Response(200, json=payload)
        if path.endswith("/interrupt") and self.cancel_mode in {"refused", "timeout", "http-error"}:
            self.requests.append(request)
            self.interrupt_entered.set()
            if self.cancel_mode == "timeout":
                await self.interrupt_release.wait()
            if self.cancel_mode == "http-error":
                return httpx.Response(503)
            self.execution("ses_one", self.inputs[0])
            return httpx.Response(200, json={"interrupted": False})
        return await super().handle(request)

    def context(self, task_id: str = "active", identity: str = "owner") -> RequestContext:
        return make_request_context(
            task_id=task_id,
            context_id="conversation",
            text="run this turn",
            call_context=ServerCallContext(state={"identity": identity}),
        )

    def start(self, task_id: str = "active") -> tuple[asyncio.Task[None], DummyEventQueue]:
        queue = DummyEventQueue()
        task = asyncio.create_task(self.executor.execute(self.context(task_id), queue))
        self.tasks.append(task)
        return task, queue

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await super().close()


@pytest_asyncio.fixture
async def server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ExecutorServer]:
    boundary = ExecutorServer(monkeypatch)
    try:
        yield boundary
    finally:
        await boundary.close()


def states(queue: DummyEventQueue) -> list[TaskState]:
    return [
        event.status.state
        for event in queue.events
        if isinstance(event, (Task, TaskStatusUpdateEvent))
    ]


@pytest.mark.asyncio
async def test_missing_cancel_context_has_no_status_or_native_side_effect(
    server: ExecutorServer,
) -> None:
    queue = DummyEventQueue()
    with pytest.raises(TaskNotCancelableError, match="known task"):
        await server.executor.cancel(RequestContext(call_context=None), queue)
    assert queue.events == []
    assert server.requests == []


@pytest.mark.asyncio
async def test_unknown_task_cannot_cancel_or_contact_native_server(server: ExecutorServer) -> None:
    queue = DummyEventQueue()
    await server.executor.cancel(server.context("unknown"), queue)
    assert queue.events == []
    assert server.requests == []


@pytest.mark.asyncio
async def test_wrong_principal_cannot_interrupt_active_owner(server: ExecutorServer) -> None:
    task, execution = server.start()
    await asyncio.wait_for(server.admitted.wait(), 1)
    queue = DummyEventQueue()
    count = len(server.requests)
    with pytest.raises(TaskNotCancelableError, match="this caller"):
        await server.executor.cancel(server.context(identity="intruder"), queue)
    assert queue.events == [] and len(server.requests) == count
    assert not task.done() and server.engine._active == 1
    server.execution("ses_one", server.inputs[0])
    await asyncio.wait_for(task, 1)
    assert states(execution) == [TaskState.TASK_STATE_COMPLETED]


@pytest.mark.asyncio
async def test_local_queued_cancel_preserves_active_owner_lock_without_interrupt(
    server: ExecutorServer,
) -> None:
    active, execution = server.start()
    await asyncio.wait_for(server.admitted.wait(), 1)
    queued, queued_execution = server.start("queued")
    async with asyncio.timeout(1):
        while ("queued", "conversation") not in server.executor._running_requests:
            await asyncio.sleep(0)
    lock = await server.executor._session_manager.get_session_lock("ses_one")
    queue = DummyEventQueue()
    await server.executor.cancel(server.context("queued"), queue)
    await asyncio.gather(queued, return_exceptions=True)
    assert queued.cancelled() and states(queue) == [TaskState.TASK_STATE_CANCELED]
    assert states(queued_execution) == [TaskState.TASK_STATE_CANCELED]
    assert ("queued", "conversation") not in server.executor._running_requests
    assert server.executor._running_requests[("active", "conversation")] is active
    assert lock.locked() and not active.done() and server.engine._active == 1
    assert len(server.inputs) == 1
    assert not any(request.url.path.endswith("/interrupt") for request in server.requests)
    assert server.client._v2_invocation_tasks["ses_one"] is active
    server.execution("ses_one", server.inputs[0])
    await asyncio.wait_for(active, 1)
    assert states(execution) == [TaskState.TASK_STATE_COMPLETED]
    assert not lock.locked() and server.engine._active == 0


@pytest.mark.asyncio
async def test_active_cancel_requires_observed_terminal_and_allows_healthy_followup(
    server: ExecutorServer,
) -> None:
    task, execution = server.start()
    await asyncio.wait_for(server.admitted.wait(), 1)
    queue = DummyEventQueue()
    await server.executor.cancel(server.context(), queue)
    await asyncio.wait_for(task, 1)
    assert states(queue) == [TaskState.TASK_STATE_CANCELED]
    assert states(execution) and set(states(execution)) == {TaskState.TASK_STATE_CANCELED}
    assert server.engine._active == 0
    assert server.engine.sessions["ses_one"].subscription.error is None
    lock = await server.executor._session_manager.get_session_lock("ses_one")
    assert not lock.locked() and server.client._v2_invocation_tasks == {}
    interrupts = [request for request in server.requests if request.url.path.endswith("/interrupt")]
    assert len(interrupts) == 1 and interrupts[0].url.params["resume"] == "false"
    server.mode = "success"
    followup, completed = server.start("followup")
    await asyncio.wait_for(followup, 1)
    assert states(completed) == [TaskState.TASK_STATE_COMPLETED]
    assert len(set(server.inputs)) == 2 and server.engine._active == 0
    settled = DummyEventQueue()
    count = len(server.requests)
    await server.executor.cancel(server.context("followup"), settled)
    assert settled.events == [] and len(server.requests) == count


@pytest.mark.asyncio
async def test_refused_interrupt_naturally_completes_without_false_canceled_status(
    server: ExecutorServer,
) -> None:
    server.cancel_mode = "refused"
    task, execution = server.start()
    await asyncio.wait_for(server.admitted.wait(), 1)
    queue = DummyEventQueue()
    await server.executor.cancel(server.context(), queue)
    await asyncio.wait_for(task, 1)
    assert states(queue) == [TaskState.TASK_STATE_COMPLETED]
    assert states(execution) == [TaskState.TASK_STATE_COMPLETED]
    assert TaskState.TASK_STATE_CANCELED not in states(execution)
    assert server.interrupt_entered.is_set() and server.engine._active == 0
    assert server.engine.sessions["ses_one"].subscription.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "http-error"])
async def test_uncertain_cancel_never_reports_canceled_and_quarantines_session(
    server: ExecutorServer, mode: str
) -> None:
    server.cancel_mode = mode
    server.executor._cancel_abort_timeout_seconds = 0.05
    task, execution = server.start()
    await asyncio.wait_for(server.admitted.wait(), 1)
    queue = DummyEventQueue()
    await server.executor.cancel(server.context(), queue)
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
    assert states(queue) == [TaskState.TASK_STATE_FAILED]
    assert TaskState.TASK_STATE_CANCELED not in states(execution)
    assert states(execution) == [TaskState.TASK_STATE_FAILED]
    assert server.interrupt_entered.is_set()
    assert server.engine.sessions["ses_one"].subscription.error is not None
    assert server.engine._active == 1
    count = len(server.requests)
    with pytest.raises(V2ExecutionUncertainError):
        await server.engine.submit("ses_one", "unsafe retry")
    assert len(server.requests) == count and len(server.inputs) == 1

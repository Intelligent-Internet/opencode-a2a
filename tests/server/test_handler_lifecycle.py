from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from a2a.server.context import ServerCallContext
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidParamsError

from opencode_a2a.server.application import OpencodeRequestHandler
from opencode_a2a.server.lifespan import build_lifespan
from opencode_a2a.server.task_store import build_task_store_runtime
from tests.support.settings import make_settings


def _context(identity="owner"):
    return ServerCallContext(state={"identity": identity})


def _request(task_id=""):
    return SendMessageRequest(
        message=Message(
            message_id="input-message",
            task_id=task_id,
            role=Role.ROLE_USER,
            parts=[Part(text="hello")],
        )
    )


def _handler(store, execute):
    return OpencodeRequestHandler(
        agent_executor=SimpleNamespace(execute=execute),
        task_store=store,
        agent_card=AgentCard(name="test", capabilities=AgentCapabilities(streaming=True)),
    )


@pytest_asyncio.fixture(params=["memory", "database"])
async def store(request, tmp_path):
    runtime = build_task_store_runtime(
        make_settings(
            a2a_task_store_backend=request.param,
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path}/tasks.db",
        )
    )
    await runtime.startup()
    try:
        yield runtime.task_store
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_unhandled_producer_failure_persists_failed_and_releases_queue(store, existing):
    async def execute(context, queue):
        raise RuntimeError("injected producer failure")

    handler = _handler(store, execute)
    params = _request("existing" if existing else "")
    context = _context()
    if existing:
        await store.save(
            Task(
                id="existing",
                context_id="ctx",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            ),
            context,
        )
    try:
        with pytest.raises(RuntimeError, match="injected producer failure"):
            await asyncio.wait_for(handler.on_message_send(params, context), 2)
        task_id = params.message.task_id
        stored = await store.get(task_id, context)
        assert stored is not None
        assert stored.status.state == TaskState.TASK_STATE_FAILED
        assert "injected producer failure" not in str(stored)
        assert await store.get(task_id, _context("other")) is None
        assert not handler._running_agents
        assert await handler._queue_manager.get(task_id) is None
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_shutdown_drains_nonblocking_work_before_closing_dependencies(store):
    async def execute(context, queue):
        await queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await asyncio.Event().wait()

    handler = _handler(store, execute)
    closed = []

    async def upstream_close():
        assert not handler._running_agents
        assert not any(not task.done() for task in handler._background_tasks)
        stored = await store.get(params.message.task_id, _context())
        assert stored.status.state == TaskState.TASK_STATE_FAILED
        closed.append("upstream")

    deps = SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock())
    lifespan = build_lifespan(
        request_handler=handler,
        database_engine=None,
        task_store_runtime=deps,
        runtime_state_runtime=deps,
        client_manager=SimpleNamespace(close_all=AsyncMock()),
        upstream_client=SimpleNamespace(close=upstream_close),
    )
    params = _request()
    params.configuration.return_immediately = True
    try:
        async with lifespan(None):
            await asyncio.wait_for(handler.on_message_send(params, _context()), 2)
        assert closed == ["upstream"]
        await handler.aclose()
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_get_task_rejects_empty_id_before_store_access():
    store = AsyncMock()
    handler = _handler(store, AsyncMock())
    with pytest.raises(InvalidParamsError):
        await handler.on_get_task(GetTaskRequest(id=""), _context())
    store.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_failure_after_disconnect_is_persisted_and_cleaned(store):
    fail = asyncio.Event()

    async def execute(context, queue):
        await queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await fail.wait()
        raise RuntimeError("detached failure")

    handler = _handler(store, execute)
    params = _request()
    stream = handler.on_message_send_stream(params, _context())
    try:
        await asyncio.wait_for(anext(stream), 2)
        await stream.aclose()
        assert handler._running_agents  # Disconnect must not cancel execution.
        fail.set()
        await asyncio.wait_for(asyncio.gather(*handler._background_tasks), 2)
        stored = await store.get(params.message.task_id, _context())
        assert stored.status.state == TaskState.TASK_STATE_FAILED
        assert not handler._running_agents
        assert not handler._managed_producers
        assert await handler._queue_manager.get(params.message.task_id) is None
    finally:
        await stream.aclose()
        await handler.aclose()


@pytest.mark.asyncio
async def test_stream_without_events_propagates_failure(store):
    handler = _handler(store, AsyncMock(side_effect=RuntimeError("stream failure")))
    params = _request()
    try:
        with pytest.raises(RuntimeError, match="stream failure"):
            async for _ in handler.on_message_send_stream(params, _context()):
                pass
        await asyncio.gather(*handler._background_tasks)
        stored = await store.get(params.message.task_id, _context())
        assert stored.status.state == TaskState.TASK_STATE_FAILED
        assert not handler._running_agents
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal", [TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_CANCELED]
)
async def test_failure_does_not_overwrite_terminal_task(store, terminal):
    saved = None

    async def execute(context, queue):
        nonlocal saved
        saved = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=terminal),
            metadata={"keep": "original"},
        )
        await store.save(saved, context.call_context)
        raise RuntimeError("late failure")

    handler = _handler(store, execute)
    params = _request()
    try:
        with pytest.raises(RuntimeError):
            await handler.on_message_send(params, _context())
        assert await store.get(params.message.task_id, _context()) == saved
        assert not handler._running_agents
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["get", "save"])
async def test_failure_to_persist_does_not_mask_producer_error_or_skip_cleanup(store, operation):
    original = getattr(store, operation)

    async def execute(context, queue):
        setattr(store, operation, AsyncMock(side_effect=RuntimeError("storage unavailable")))
        raise RuntimeError("original execution failure")

    handler = _handler(store, execute)
    params = _request()
    try:
        with pytest.raises(RuntimeError, match="original execution failure"):
            await handler.on_message_send(params, _context())
        assert not handler._running_agents
        assert not handler._managed_producers
        assert await handler._queue_manager.get(params.message.task_id) is None
    finally:
        setattr(store, operation, original)
        await handler.aclose()


@pytest.mark.asyncio
async def test_close_waits_for_setup_and_rejects_new_requests(store):
    entered = asyncio.Event()
    release = asyncio.Event()
    handler = _handler(store, AsyncMock())
    original = handler._request_context_builder.build

    async def blocked_build(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    handler._request_context_builder.build = blocked_build
    setup = asyncio.create_task(handler._setup_message_execution(_request(), _context()))
    await entered.wait()
    close = asyncio.create_task(handler.aclose())
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    _, task_id, queue, _, producer = await setup
    await asyncio.wait_for(close, 2)
    assert producer.done()
    assert queue.is_closed()
    assert not handler._running_agents
    assert not handler._managed_producers
    assert await handler._queue_manager.get(task_id) is None
    from a2a.types import InternalError

    with pytest.raises(InternalError, match="shutting down"):
        await handler.on_message_send(_request(), _context())
    await handler.aclose()


@pytest.mark.asyncio
async def test_cancelled_close_waiter_still_waits_for_drain(store):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def execute(context, queue):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await release.wait()

    handler = _handler(store, execute)
    await handler._setup_message_execution(_request(), _context())
    await started.wait()
    close = asyncio.create_task(handler.aclose())
    await cancelled.wait()
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(close, 2)
    assert not handler._running_agents
    await handler.aclose()


@pytest.mark.asyncio
async def test_lifespan_attempts_all_cleanup_after_failure():
    calls = []

    def callback(name, fail=False):
        async def run():
            calls.append(name)
            if fail:
                raise RuntimeError(name)

        return run

    lifespan = build_lifespan(
        request_handler=SimpleNamespace(aclose=callback("handler", fail=True)),
        database_engine=SimpleNamespace(dispose=callback("engine")),
        task_store_runtime=SimpleNamespace(
            startup=callback("task-start"), shutdown=callback("task-stop")
        ),
        runtime_state_runtime=SimpleNamespace(
            startup=callback("state-start"), shutdown=callback("state-stop")
        ),
        client_manager=SimpleNamespace(close_all=callback("client", fail=True)),
        upstream_client=SimpleNamespace(close=callback("upstream")),
    )
    with pytest.raises(RuntimeError, match="client"):
        async with lifespan(None):
            pass
    assert calls == [
        "task-start",
        "state-start",
        "handler",
        "client",
        "upstream",
        "state-stop",
        "task-stop",
        "engine",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_length", [None, 0, 1, 10])
async def test_get_task_preserves_stored_history_and_identity(store, history_length):
    from a2a.types import TaskNotFoundError

    context = _context()
    original = Task(
        id="history-task",
        context_id="ctx",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        history=[
            Message(message_id=f"msg-{i}", role=Role.ROLE_USER, parts=[Part(text=str(i))])
            for i in range(3)
        ],
    )
    await store.save(original, context)
    handler = _handler(store, AsyncMock())
    params = GetTaskRequest(id=original.id)
    if history_length is not None:
        params.history_length = history_length
    result = await handler.on_get_task(params, context)
    expected_length = 3 if history_length is None else min(3, history_length)
    assert len(result.history) == expected_length
    if history_length == 1:
        assert result.history[0].message_id == "msg-2"
    assert await store.get(original.id, context) == original
    with pytest.raises(TaskNotFoundError):
        await handler.on_get_task(params, _context("other"))


@pytest.mark.asyncio
async def test_get_task_rejects_negative_history_before_store_access():
    store = AsyncMock()
    handler = _handler(store, AsyncMock())
    with pytest.raises(InvalidParamsError):
        await handler.on_get_task(GetTaskRequest(id="task", history_length=-1), _context())
    store.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_close_cleans_other_tasks_when_queue_cleanup_fails(store):
    started = asyncio.Event()

    async def execute(context, queue):
        started.set()
        await asyncio.Event().wait()

    handler = _handler(store, execute)
    await handler._setup_message_execution(_request(), _context())
    await started.wait()
    background = asyncio.create_task(asyncio.Event().wait())
    handler._track_background_task(background)
    original = handler._queue_manager.close

    async def failing_close(task_id):
        await original(task_id)
        raise RuntimeError("queue cleanup failed")

    handler._queue_manager.close = failing_close
    with pytest.raises(ExceptionGroup, match="Request handler cleanup failed"):
        await handler.aclose()
    assert background.done()
    assert not handler._running_agents
    assert not handler._managed_producers


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_start", ["task", "state"])
async def test_partial_startup_still_closes_resources(failed_start):
    task_runtime = SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock())
    state_runtime = SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock())
    runtime = task_runtime if failed_start == "task" else state_runtime
    runtime.startup.side_effect = RuntimeError("startup failed")
    handler, client, upstream, engine = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
    lifespan = build_lifespan(
        request_handler=handler,
        database_engine=engine,
        task_store_runtime=task_runtime,
        runtime_state_runtime=state_runtime,
        client_manager=client,
        upstream_client=upstream,
    )
    with pytest.raises(RuntimeError, match="startup failed"):
        async with lifespan(None):
            pytest.fail("Startup must fail before yielding")
    handler.aclose.assert_awaited_once()
    client.close_all.assert_awaited_once()
    upstream.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    assert task_runtime.shutdown.await_count == (failed_start == "state")
    state_runtime.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_cancel_remains_canceled_and_idempotent(store):
    from a2a.types import CancelTaskRequest, TaskStatusUpdateEvent

    async def execute(context, queue):
        await queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await asyncio.Event().wait()

    async def cancel(context, queue):
        await queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )

    handler = _handler(store, execute)
    handler.agent_executor.cancel = cancel
    params = _request()
    params.configuration.return_immediately = True
    try:
        task = await handler.on_message_send(params, _context())
        request = CancelTaskRequest(id=task.id)
        result = await handler.on_cancel_task(request, _context())
        assert result.status.state == TaskState.TASK_STATE_CANCELED
        assert await handler.on_cancel_task(request, _context()) == result
        await handler.aclose()
        stored = await store.get(task.id, _context())
        assert stored.status.state == TaskState.TASK_STATE_CANCELED
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_app_wires_shutdown_and_get_task_validation(monkeypatch):
    import httpx

    from opencode_a2a.server.application import create_app

    app = create_app(make_settings(a2a_task_store_backend="memory"))
    handler = app.state._jsonrpc_app._http_handler

    async def execute(context, queue):
        await queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.agent_executor, "execute", execute)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            invalid = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "GetTask",
                    "params": {"id": ""},
                },
            )
            assert invalid.json()["error"]["code"] == -32602
            response = await client.post(
                "/message:send",
                json={
                    "message": {
                        "messageId": "app-request",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                    },
                    "configuration": {"returnImmediately": True},
                },
            )
            assert response.status_code == 200, response.text
            assert handler._running_agents
    assert not handler._running_agents
    assert not handler._managed_producers
    assert not any(not task.done() for task in handler._background_tasks)


@pytest.mark.asyncio
async def test_shutdown_drains_background_tasks_created_during_cancellation(store):
    handler = _handler(store, AsyncMock())
    started = asyncio.Event()
    late_tasks = []

    async def consume():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            late = asyncio.create_task(asyncio.Event().wait())
            late_tasks.append(late)
            handler._track_background_task(late)

    consumer = asyncio.create_task(consume())
    handler._track_background_task(consumer)
    await started.wait()
    await asyncio.wait_for(asyncio.gather(handler.aclose(), handler.aclose()), 2)
    assert consumer.done()
    assert len(late_tasks) == 1
    assert late_tasks[0].done()
    assert not any(not task.done() for task in handler._background_tasks)

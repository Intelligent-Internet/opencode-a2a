from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from a2a.server.context import ServerCallContext
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError

from opencode_a2a.server.request_handler import OpencodeRequestHandler
from opencode_a2a.server.task_store import (
    build_task_store_runtime,
    task_store_save,
)
from opencode_a2a.trace_context import TraceContext, bind_trace_context, get_current_trace_context
from tests.support.settings import make_settings


def _context(identity: str = "owner") -> ServerCallContext:
    return ServerCallContext(state={"identity": identity})


def _request(task_id: str = "") -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id="input-message",
            task_id=task_id,
            role=Role.ROLE_USER,
            parts=[Part(text="hello")],
        )
    )


def _agent_card() -> AgentCard:
    return AgentCard(name="test", capabilities=AgentCapabilities(streaming=True))


def _handler(runtime, execute, cancel=None) -> OpencodeRequestHandler:  # noqa: ANN001
    return OpencodeRequestHandler(
        agent_executor=SimpleNamespace(execute=execute, cancel=cancel or AsyncMock()),
        task_store=runtime.task_store,
        event_stream=runtime.event_stream,
        output_modes=runtime.output_modes,
        agent_card=_agent_card(),
    )


async def _wait_for_active_tasks_to_release(
    handler: OpencodeRequestHandler,
    *,
    timeout: float = 1.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while handler._active_task_registry._active_tasks:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("active tasks were not released")
        await asyncio.sleep(0.01)


@pytest_asyncio.fixture(params=["memory", "database"])
async def runtime(request, tmp_path: Path):
    runtime = build_task_store_runtime(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend=request.param,
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}",
        )
    )
    await runtime.startup()
    try:
        yield runtime
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_early_failure_persists_failed_task(runtime) -> None:
    captured: dict[str, Any] = {}

    async def execute(context, queue):  # noqa: ANN001
        del queue
        captured["context"] = context
        raise RuntimeError("injected producer failure")

    handler = _handler(runtime, execute)
    params = _request()
    try:
        with pytest.raises(RuntimeError, match="injected producer failure"):
            await handler.on_message_send(params, _context())
        request_context = captured["context"]
        deadline = asyncio.get_running_loop().time() + 1.0
        while True:
            stored = await handler.on_get_task(
                GetTaskRequest(id=request_context.task_id),
                _context(),
            )
            if stored is not None:
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.01)
        assert stored is not None
        assert stored.status.state == TaskState.TASK_STATE_FAILED
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_terminal_send_is_unsupported(runtime) -> None:
    await task_store_save(
        runtime.task_store,
        Task(
            id="terminal-task",
            context_id="ctx",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        ),
        _context(),
    )
    execute = AsyncMock()
    handler = _handler(runtime, execute)
    try:
        with pytest.raises(UnsupportedOperationError, match="TASK_STATE_COMPLETED"):
            await handler.on_message_send(_request("terminal-task"), _context())
        execute.assert_not_awaited()
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_direct_message_releases_active_task(runtime, streaming: bool) -> None:
    async def execute(context, queue):  # noqa: ANN001
        await queue.enqueue_event(
            Message(
                message_id="direct-response",
                context_id=context.context_id,
                role=Role.ROLE_AGENT,
                parts=[Part(text="direct response")],
            )
        )

    handler = _handler(runtime, execute)
    try:
        if streaming:
            events = [
                event async for event in handler.on_message_send_stream(_request(), _context())
            ]
            assert len(events) == 1
            assert isinstance(events[0], Message)
        else:
            result = await handler.on_message_send(_request(), _context())
            assert isinstance(result, Message)
        await _wait_for_active_tasks_to_release(handler)
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_follow_up_message_uses_sender_contextvars(runtime) -> None:
    class _FollowUpExecutor:
        def __init__(self) -> None:
            self.seen_trace_ids: list[str | None] = []

        async def execute(self, context, queue):  # noqa: ANN001
            trace_context = get_current_trace_context()
            self.seen_trace_ids.append(
                trace_context.trace_id if trace_context is not None else None
            )
            if context.current_task is not None:
                await TaskUpdater(queue, context.task_id, context.context_id).complete()
                return
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message],
            )
            await queue.enqueue_event(task)
            updater = TaskUpdater(queue, task.id, task.context_id)
            await updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message=updater.new_agent_message([Part(text="more input required")]),
            )

    executor = _FollowUpExecutor()
    handler = _handler(runtime, executor.execute)
    first_trace_id = "1" * 32
    second_trace_id = "2" * 32
    try:
        with bind_trace_context(
            TraceContext(
                traceparent=f"00-{first_trace_id}-{'0' * 16}-01",
                trace_id=first_trace_id,
            )
        ):
            first = await handler.on_message_send(_request(), _context())
        assert isinstance(first, Task)
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        follow_up = _request(first.id)
        follow_up.message.message_id = "follow-up-message"
        follow_up.message.context_id = first.context_id
        with bind_trace_context(
            TraceContext(
                traceparent=f"00-{second_trace_id}-{'0' * 16}-01",
                trace_id=second_trace_id,
            )
        ):
            second = await handler.on_message_send(follow_up, _context())
        assert isinstance(second, Task)
        assert second.status.state == TaskState.TASK_STATE_COMPLETED
        assert executor.seen_trace_ids == [first_trace_id, second_trace_id]
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_canceled_task(runtime) -> None:
    await task_store_save(
        runtime.task_store,
        Task(
            id="canceled-task",
            context_id="ctx",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        _context(),
    )
    await task_store_save(
        runtime.task_store,
        Task(
            id="canceled-task",
            context_id="ctx",
            status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
        ),
        _context(),
    )
    handler = _handler(runtime, AsyncMock())
    try:
        result = await handler.on_cancel_task(CancelTaskRequest(id="canceled-task"), _context())
        assert result is not None
        assert result.status.state == TaskState.TASK_STATE_CANCELED
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_subscribe_terminal_task_is_unsupported(runtime) -> None:
    await task_store_save(
        runtime.task_store,
        Task(
            id="terminal-subscribe",
            context_id="ctx",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        ),
        _context(),
    )
    handler = _handler(runtime, AsyncMock())
    try:
        with pytest.raises(UnsupportedOperationError):
            async for _event in handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id="terminal-subscribe"),
                _context(),
            ):
                pass
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_get_missing_task_raises_not_found(runtime) -> None:
    handler = _handler(runtime, AsyncMock())
    try:
        with pytest.raises(TaskNotFoundError):
            await handler.on_get_task(GetTaskRequest(id="missing"), _context())
    finally:
        await handler.aclose()


@pytest.mark.asyncio
async def test_handler_close_drains_active_producer(runtime) -> None:
    started = asyncio.Event()

    async def execute(context, queue):  # noqa: ANN001
        await queue.enqueue_event(
            Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        started.set()
        await asyncio.Event().wait()

    handler = _handler(runtime, execute)
    params = _request()
    params.configuration.return_immediately = True
    result = await handler.on_message_send(params, _context())
    assert isinstance(result, Task)
    await started.wait()
    await handler.aclose()

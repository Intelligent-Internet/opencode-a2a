import asyncio
import types
from unittest.mock import AsyncMock

import pytest
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    Part,
    SubscribeToTaskRequest,
    Task,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
)

from opencode_a2a.server.application import OpencodeRequestHandler


def _task(*, task_id: str, context_id: str, state: TaskState) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
    )


def _store() -> InMemoryTaskStore:
    return InMemoryTaskStore(owner_resolver=lambda _context: "test-owner")


def _agent_card() -> AgentCard:
    return AgentCard(name="opencode-a2a", capabilities=AgentCapabilities(streaming=True))


def _message_send_params(*, text: str = "hello") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        configuration=None,
        message=types.SimpleNamespace(parts=[Part(text=text)]),
    )


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_already_canceled_task() -> None:
    executor = AsyncMock()
    store = _store()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
    )
    task = _task(task_id="task-1", context_id="ctx-1", state=TaskState.TASK_STATE_CANCELED)
    await store.save(task, None)

    result = await handler.on_cancel_task(CancelTaskRequest(id="task-1"))

    assert result is not None
    assert result.status.state == TaskState.TASK_STATE_CANCELED
    executor.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_rejects_completed_task() -> None:
    executor = AsyncMock()
    store = _store()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
    )
    task = _task(task_id="task-2", context_id="ctx-2", state=TaskState.TASK_STATE_COMPLETED)
    await store.save(task, None)

    with pytest.raises(TaskNotCancelableError):
        await handler.on_cancel_task(CancelTaskRequest(id="task-2"))

    executor.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_is_race_safe_when_task_becomes_canceled_during_super_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = AsyncMock()
    store = _store()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
    )
    task = _task(task_id="task-race", context_id="ctx-race", state=TaskState.TASK_STATE_WORKING)
    await store.save(task, None)

    async def _mark_task_canceled(_request_context, _queue):  # noqa: ANN001
        await store.save(
            _task(task_id="task-race", context_id="ctx-race", state=TaskState.TASK_STATE_CANCELED),
            None,
        )

    async def _consume_non_canceled(_self, _consumer):  # noqa: ANN001
        return task

    executor.cancel.side_effect = _mark_task_canceled
    monkeypatch.setattr(handler._queue_manager, "tap", AsyncMock(return_value=object()))  # noqa: SLF001
    monkeypatch.setattr(
        "opencode_a2a.server.application.ResultAggregator.consume_all",
        _consume_non_canceled,
    )

    result = await handler.on_cancel_task(CancelTaskRequest(id="task-race"))

    assert result is not None
    assert result.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.asyncio
async def test_resubscribe_terminal_task_replays_final_snapshot_once() -> None:
    executor = AsyncMock()
    store = _store()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
    )
    task = _task(task_id="task-3", context_id="ctx-3", state=TaskState.TASK_STATE_CANCELED)
    await store.save(task, None)

    events = []
    async for event in handler.on_subscribe_to_task(SubscribeToTaskRequest(id="task-3")):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], Task)
    assert events[0].status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.asyncio
async def test_resubscribe_non_terminal_without_queue_keeps_not_found_behavior() -> None:
    executor = AsyncMock()
    store = _store()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
    )
    task = _task(task_id="task-4", context_id="ctx-4", state=TaskState.TASK_STATE_WORKING)
    await store.save(task, None)

    with pytest.raises(TaskNotFoundError):
        async for _event in handler.on_subscribe_to_task(SubscribeToTaskRequest(id="task-4")):
            pass


@pytest.mark.asyncio
async def test_message_send_tracks_background_consumer_from_sdk_interrupt_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = AsyncMock()
    store = _store()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
    )

    result_task = _task(
        task_id="task-5", context_id="ctx-5", state=TaskState.TASK_STATE_INPUT_REQUIRED
    )
    producer_task = asyncio.create_task(asyncio.sleep(0))
    bg_task = asyncio.create_task(asyncio.sleep(0))

    class _FakeAggregator:
        async def consume_and_break_on_interrupt(  # noqa: ANN001
            self, consumer, *, blocking, event_callback
        ):
            del consumer, blocking, event_callback
            return result_task, True, bg_task

    async def _fake_setup_message_execution(params, context=None):  # noqa: ANN001
        del params, context
        return None, "task-5", object(), _FakeAggregator(), producer_task

    tracked: list[asyncio.Task] = []

    async def _fake_cleanup_producer(task, task_id):  # noqa: ANN001
        del task, task_id
        return None

    def _fake_track_background_task(task: asyncio.Task) -> None:
        tracked.append(task)

    monkeypatch.setattr(handler, "_setup_message_execution", _fake_setup_message_execution)
    monkeypatch.setattr(handler, "_cleanup_producer", _fake_cleanup_producer)
    monkeypatch.setattr(handler, "_track_background_task", _fake_track_background_task)

    result = await handler.on_message_send(_message_send_params())

    assert result is result_task
    assert [task.get_name() for task in tracked] == [
        "continue_consuming:task-5",
        "cleanup_producer:task-5",
    ]
    await asyncio.gather(*tracked)

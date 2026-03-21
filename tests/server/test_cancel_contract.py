import asyncio
from unittest.mock import AsyncMock

import pytest
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskIdParams,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.utils.errors import ServerError

from opencode_a2a_server.server.application import OpencodeRequestHandler


def _task(*, task_id: str, context_id: str, state: TaskState) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
    )


def _message_send_params(*, text: str = "hello") -> MessageSendParams:
    return MessageSendParams(
        message={
            "messageId": "msg-1",
            "role": Role.user,
            "parts": [Part(root=TextPart(text=text))],
        }
    )


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_already_canceled_task() -> None:
    executor = AsyncMock()
    store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(agent_executor=executor, task_store=store)
    task = _task(task_id="task-1", context_id="ctx-1", state=TaskState.canceled)
    await store.save(task)

    result = await handler.on_cancel_task(TaskIdParams(id="task-1"))

    assert result is not None
    assert result.status.state == TaskState.canceled
    executor.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_rejects_completed_task() -> None:
    executor = AsyncMock()
    store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(agent_executor=executor, task_store=store)
    task = _task(task_id="task-2", context_id="ctx-2", state=TaskState.completed)
    await store.save(task)

    with pytest.raises(ServerError) as exc:
        await handler.on_cancel_task(TaskIdParams(id="task-2"))

    assert isinstance(exc.value.error, TaskNotCancelableError)
    executor.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_is_race_safe_when_task_becomes_canceled_during_super_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = AsyncMock()
    store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(agent_executor=executor, task_store=store)
    task = _task(task_id="task-race", context_id="ctx-race", state=TaskState.working)
    await store.save(task)

    async def _fake_super_cancel(_self, params: TaskIdParams, context=None):  # noqa: ANN001
        await store.save(_task(task_id=params.id, context_id="ctx-race", state=TaskState.canceled))
        raise ServerError(error=TaskNotCancelableError(message="task already canceled"))

    monkeypatch.setattr(DefaultRequestHandler, "on_cancel_task", _fake_super_cancel)

    result = await handler.on_cancel_task(TaskIdParams(id="task-race"))

    assert result is not None
    assert result.status.state == TaskState.canceled


@pytest.mark.asyncio
async def test_resubscribe_terminal_task_replays_final_snapshot_once() -> None:
    executor = AsyncMock()
    store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(agent_executor=executor, task_store=store)
    task = _task(task_id="task-3", context_id="ctx-3", state=TaskState.canceled)
    await store.save(task)

    events = []
    async for event in handler.on_resubscribe_to_task(TaskIdParams(id="task-3")):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], Task)
    assert events[0].status.state == TaskState.canceled


@pytest.mark.asyncio
async def test_resubscribe_non_terminal_without_queue_keeps_not_found_behavior() -> None:
    executor = AsyncMock()
    store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(agent_executor=executor, task_store=store)
    task = _task(task_id="task-4", context_id="ctx-4", state=TaskState.working)
    await store.save(task)

    with pytest.raises(ServerError) as exc:
        async for _event in handler.on_resubscribe_to_task(TaskIdParams(id="task-4")):
            pass

    assert isinstance(exc.value.error, TaskNotFoundError)


@pytest.mark.asyncio
async def test_message_send_tracks_background_consumer_from_sdk_interrupt_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = AsyncMock()
    store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(agent_executor=executor, task_store=store)

    result_task = _task(task_id="task-5", context_id="ctx-5", state=TaskState.input_required)
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

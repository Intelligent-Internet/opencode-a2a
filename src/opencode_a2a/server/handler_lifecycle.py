from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext, RequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueueLegacy
from a2a.server.events.queue_manager import NoTaskQueue
from a2a.server.request_handlers.default_request_handler import LegacyRequestHandler
from a2a.server.tasks import PushNotificationConfigStore, PushNotificationSender, TaskStore
from a2a.types import AgentCard, InternalError, Task, TaskState, TaskStatus

from ..task_states import TERMINAL_TASK_STATES

logger = logging.getLogger(__name__)


class ManagedLegacyRequestHandler(LegacyRequestHandler):
    """Own producer and background-task lifetimes around the pinned SDK legacy handler."""

    def __init__(  # noqa: PLR0913
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        agent_card: AgentCard,
        queue_manager: Any | None = None,
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        request_context_builder: RequestContextBuilder | None = None,
        extended_agent_card: AgentCard | None = None,
        extended_card_modifier: Callable[[AgentCard, ServerCallContext], Awaitable[AgentCard]]
        | None = None,
    ) -> None:
        super().__init__(
            agent_executor=agent_executor,
            task_store=task_store,
            agent_card=agent_card,
            queue_manager=queue_manager,
            push_config_store=push_config_store,
            push_sender=push_sender,
            request_context_builder=request_context_builder,
            extended_agent_card=extended_agent_card,
            extended_card_modifier=extended_card_modifier,
        )
        self._setup_lock = asyncio.Lock()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._managed_producers: dict[asyncio.Task, str] = {}
        self._draining_consumers: set[asyncio.Task] = set()
        self._shutdown_failures: list[RequestContext] = []

    async def _persist_execution_failure(self, request: RequestContext) -> None:
        """Write independently of the closing queue, preserving terminal snapshots."""
        if not request.task_id or not request.context_id:
            return
        try:
            existing = await self.task_store.get(request.task_id, request.call_context)
            if existing and existing.status.state in TERMINAL_TASK_STATES:
                return
            task = Task(id=request.task_id, context_id=request.context_id)
            if existing:
                task.CopyFrom(existing)
            if request.message is not None and not any(
                message.message_id == request.message.message_id for message in task.history
            ):
                task.history.append(request.message)
            task.status.CopyFrom(TaskStatus(state=TaskState.TASK_STATE_FAILED))
            task.status.timestamp.GetCurrentTime()
            await self.task_store.save(task, request.call_context)
        except Exception as exc:
            # Preserve the producer error without logging storage payloads or credentials.
            logger.error(
                "Could not persist execution failure task_id=%s error_type=%s",
                request.task_id,
                type(exc).__name__,
            )

    async def _run_event_stream(self, request: RequestContext, queue: EventQueueLegacy) -> None:
        try:
            await self.agent_executor.execute(request, queue)
        except asyncio.CancelledError:
            if self._closing:
                self._shutdown_failures.append(request)
            raise
        except Exception as exc:
            logger.error(
                "Agent producer failed task_id=%s error_type=%s",
                request.task_id,
                type(exc).__name__,
            )
            if self._closing:
                self._shutdown_failures.append(request)
            else:
                await self._persist_execution_failure(request)
            raise
        finally:
            await queue.close()

    async def _cleanup_producer(self, producer_task: asyncio.Task, task_id: str) -> None:
        try:
            # The consumer reports execution failures. They must not skip cleanup.
            with suppress(asyncio.CancelledError, Exception):
                await producer_task
        finally:
            try:
                with suppress(NoTaskQueue):
                    await self._queue_manager.close(task_id)
            finally:
                with self._running_agents_lock:
                    if self._running_agents.get(task_id) is producer_task:
                        self._running_agents.pop(task_id, None)
                self._managed_producers.pop(producer_task, None)

    def _track_consumer_task(self, task: asyncio.Task) -> None:
        # These consumers can still be persisting output after the producer exits.
        self._draining_consumers.add(task)
        task.add_done_callback(self._draining_consumers.discard)
        self._track_background_task(task)

    def _track_background_task(self, task: asyncio.Task) -> None:
        super()._track_background_task(task)
        if self._closing and task not in self._draining_consumers:
            task.cancel()

    async def aclose(self) -> None:
        """Stop local execution before its clients and stores are closed."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain_execution())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                if self._close_task.cancelled():
                    raise
                # Repeated cancellation must not let lifespan close dependencies early.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _drain_execution(self) -> None:
        # A setup already in flight must register its producer before the snapshot.
        async with self._setup_lock:
            producers = dict(self._managed_producers)
            producers.update((task, task_id) for task_id, task in self._running_agents.items())
        for task in producers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*producers, return_exceptions=True)
        results = await asyncio.gather(
            *(self._cleanup_producer(task, task_id) for task, task_id in producers.items()),
            return_exceptions=True,
        )
        # Queues are closed, so consumers finish naturally after persisting buffered
        # output. Cancelling them here could leave a successful task stuck WORKING.
        # Other background work can be cancelled and may register further cleanup.
        while pending := [
            task for task in self._background_tasks | self._draining_consumers if not task.done()
        ]:
            for task in pending:
                if task not in self._draining_consumers:
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        # First-terminal-wins must not freeze a partial snapshot before buffered
        # artifacts or a completed status have reached the store.
        for request in self._shutdown_failures:
            await self._persist_execution_failure(request)
        self._shutdown_failures.clear()
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("Request handler cleanup failed", errors)

    async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
        async with self._setup_lock:
            if self._closing:
                raise InternalError(message="Server is shutting down.")
            result = await super()._setup_message_execution(params, context)
            _manager, task_id, _queue, _aggregator, producer_task = result
            self._managed_producers[producer_task] = task_id
            return result

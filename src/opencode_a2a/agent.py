from __future__ import annotations

import asyncio
import logging
import uuid

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    Artifact,
    Message,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)


class OpencodeAgentExecutor(AgentExecutor):
    def __init__(self, client: OpencodeClient) -> None:
        self._client = client
        self._sessions: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise RuntimeError("Missing task_id or context_id in request context")

        user_text = context.get_user_input().strip()
        if not user_text:
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message="Only text input is supported.",
            )
            return

        session_id = await self._get_or_create_session(task_id, user_text)

        try:
            response = await self._client.send_message(session_id, user_text)
            response_text = response.text or "(No text content returned by OpenCode.)"
            assistant_message = _build_assistant_message(
                task_id=task_id,
                context_id=context_id,
                text=response_text,
            )
            artifact = Artifact(
                artifact_id=str(uuid.uuid4()),
                name="response",
                parts=[TextPart(text=response_text)],
            )
            history = _build_history(context)
            task = Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.input_required),
                history=history,
                artifacts=[artifact],
                metadata={
                    "opencode": {
                        "session_id": response.session_id,
                        "message_id": response.message_id,
                    }
                },
            )
            # Attach the assistant message as the current status message.
            task.status.message = assistant_message
            await event_queue.enqueue_event(task)
        except Exception as exc:
            logger.exception("OpenCode request failed")
            await self._emit_error(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                message=f"OpenCode error: {exc}",
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise RuntimeError("Missing task_id or context_id in request context")

        event = TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.canceled),
            final=True,
        )
        await event_queue.enqueue_event(event)
        await event_queue.close()
        self._sessions.pop(task_id, None)

    async def _get_or_create_session(self, task_id: str, title: str) -> str:
        async with self._lock:
            existing = self._sessions.get(task_id)
            if existing:
                return existing
            session_id = await self._client.create_session(title=title)
            self._sessions[task_id] = session_id
            return session_id

    async def _emit_error(
        self,
        event_queue: EventQueue,
        task_id: str,
        context_id: str,
        message: str,
    ) -> None:
        error_message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.agent,
            parts=[TextPart(text=message)],
            task_id=task_id,
            context_id=context_id,
        )
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.failed, message=error_message),
            history=[error_message],
        )
        await event_queue.enqueue_event(task)


def _build_assistant_message(task_id: str, context_id: str, text: str) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.agent,
        parts=[TextPart(text=text)],
        task_id=task_id,
        context_id=context_id,
    )


def _build_history(context: RequestContext) -> list[Message]:
    if context.current_task and context.current_task.history:
        history = list(context.current_task.history)
    else:
        history = []
        if context.message:
            history.append(context.message)
    # Do not append assistant message to history; it lives in status.message.
    return history

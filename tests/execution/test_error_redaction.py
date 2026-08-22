from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events.event_queue import EventQueue
from a2a.types import TaskArtifactUpdateEvent, TaskState

from opencode_a2a.execution.executor import OpencodeAgentExecutor
from opencode_a2a.redact import REDACTED_PATH_PLACEHOLDER


def _part_text(part) -> str:  # noqa: ANN001
    return getattr(part, "text", None) or getattr(getattr(part, "root", None), "text", "")


@pytest.mark.asyncio
async def test_emit_error_redacts_absolute_paths() -> None:
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    event_queue = AsyncMock(spec=EventQueue)

    await executor._emit_error(
        event_queue,
        task_id="task-1",
        context_id="context-1",
        message="Cannot open session file '/home/ubuntu/sessions/session-1.json': No such file",
        state=TaskState.TASK_STATE_FAILED,
        streaming_request=False,
    )

    task = event_queue.enqueue_event.call_args[0][0]
    text = _part_text(task.status.message.parts[0])
    assert REDACTED_PATH_PLACEHOLDER in text
    assert "/home/ubuntu/sessions/session-1.json" not in text


@pytest.mark.asyncio
async def test_emit_error_redacts_paths_in_streaming_artifact() -> None:
    client = MagicMock()
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    event_queue = AsyncMock(spec=EventQueue)

    await executor._emit_error(
        event_queue,
        task_id="task-2",
        context_id="context-2",
        message="Timeout writing to /var/log/opencode/app.log",
        state=TaskState.TASK_STATE_FAILED,
        streaming_request=True,
    )

    events = [call.args[0] for call in event_queue.enqueue_event.call_args_list]
    artifact_event = next(event for event in events if isinstance(event, TaskArtifactUpdateEvent))
    text = _part_text(artifact_event.artifact.parts[0])
    assert REDACTED_PATH_PLACEHOLDER in text
    assert "/var/log/opencode/app.log" not in text

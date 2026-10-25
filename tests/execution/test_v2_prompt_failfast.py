from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from a2a.types import Task, TaskState

from opencode_a2a.execution.executor import OpencodeAgentExecutor
from tests.support.helpers import DummyEventQueue, make_request_context
from tests.support.settings import make_settings


@pytest.mark.asyncio
async def test_v2_prompt_is_failed_before_session_or_upstream_side_effect() -> None:
    client = AsyncMock()
    client.settings = make_settings(opencode_api_version="v2")
    client.directory = "/tmp/opencode-v2-workspace"
    client.prompt_execution_supported = False
    client.prompt_execution_unsupported_reason = (
        "V2 input ID identifies its user message, but active cancellation cannot be proven."
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-v2", context_id="ctx-v2", text="hello"),
        queue,
    )

    tasks = [event for event in queue.events if isinstance(event, Task)]
    assert tasks
    assert tasks[-1].status.state == TaskState.TASK_STATE_FAILED
    assert "active cancellation cannot be proven" in tasks[-1].status.message.parts[0].text
    client.create_session.assert_not_awaited()
    client.send_message.assert_not_awaited()
    client.submit_prompt.assert_not_awaited()
    client.stream_events.assert_not_awaited()
    client.abort_session.assert_not_awaited()

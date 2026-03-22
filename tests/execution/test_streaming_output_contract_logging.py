import logging

import pytest

from opencode_a2a.execution.executor import (
    OpencodeAgentExecutor,
)
from tests.support.helpers import (
    DummyEventQueue,
    make_request_context,
    make_settings,
)
from tests.support.streaming_output import (
    DummyStreamingClient,
    _artifact_stream_meta,
    _artifact_updates,
    _event,
)


@pytest.mark.asyncio
async def test_streaming_logs_raw_upstream_events_at_debug(caplog) -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="tool",
                delta="",
                part_id="prt-tool-debug",
                part_overrides={
                    "callID": "call-debug",
                    "tool": "bash",
                    "state": {"status": "running"},
                    "text": "x" * 80,
                },
            )
        ],
        response_text="done",
    )
    client.settings = make_settings(
        a2a_bearer_token="test",
        opencode_base_url="http://localhost",
        a2a_log_body_limit=64,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.execution"):
        await executor.execute(
            make_request_context(task_id="task-debug", context_id="ctx-debug", text="go"),
            queue,
        )

    messages = [record.message for record in caplog.records]
    assert any("OpenCode stream event type=message.part.updated" in message for message in messages)
    assert any("part_type=tool" in message for message in messages)
    assert any("part_id=prt-tool-debug" in message for message in messages)
    assert any("payload=" in message and "[truncated]" in message for message in messages)


@pytest.mark.asyncio
async def test_streaming_logs_interrupt_payload_at_debug_with_redaction(caplog) -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            {
                "type": "permission.asked",
                "properties": {
                    "id": "perm-debug-1",
                    "sessionID": "ses-1",
                    "permission": "bash",
                    "patterns": ["git push origin main"],
                    "request": {
                        "description": "Agent wants to push the branch.",
                    },
                    "context": {
                        "reason": "The fix is ready for review.",
                    },
                    "prompt": {
                        "message": "Allow the agent to push?",
                    },
                    "accessToken": "super-secret-token",
                },
            }
        ],
        response_text="done",
    )
    client.settings = make_settings(
        a2a_bearer_token="test",
        opencode_base_url="http://localhost",
        a2a_log_body_limit=0,
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.execution"):
        await executor.execute(
            make_request_context(
                task_id="task-interrupt-debug", context_id="ctx-interrupt-debug", text="go"
            ),
            queue,
        )

    messages = [record.message for record in caplog.records]
    assert any("OpenCode stream event type=permission.asked" in message for message in messages)
    assert any(
        '"request":{"description":"Agent wants to push the branch."}' in message
        for message in messages
    )
    assert any(
        '"context":{"reason":"The fix is ready for review."}' in message for message in messages
    )
    assert any('"prompt":{"message":"Allow the agent to push?"}' in message for message in messages)
    assert any('"accessToken":"[redacted]"' in message for message in messages)
    assert not any("super-secret-token" in message for message in messages)


@pytest.mark.asyncio
async def test_streaming_does_not_log_raw_upstream_events_above_debug(caplog) -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                delta="thinking",
                part_id="prt-no-debug",
            )
        ],
        response_text="done",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    with caplog.at_level(logging.INFO, logger="opencode_a2a.execution"):
        await executor.execute(
            make_request_context(task_id="task-no-debug", context_id="ctx-no-debug", text="go"),
            queue,
        )

    assert not any("OpenCode stream event type=" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_streaming_keeps_multiple_message_ids_in_same_request_window() -> None:
    client = DummyStreamingClient(
        stream_events_payload=[
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="reasoning",
                part_id="prt-m1",
                message_id="msg-a",
                delta="step one ",
            ),
            _event(
                session_id="ses-1",
                role="assistant",
                part_type="text",
                part_id="prt-m2",
                message_id="msg-b",
                delta="final answer",
            ),
        ],
        response_text="final answer",
        response_message_id="msg-b",
    )
    executor = OpencodeAgentExecutor(client, streaming_enabled=True)
    executor._should_stream = lambda context: True  # type: ignore[method-assign]
    queue = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="task-multi-mid", context_id="ctx-multi-mid", text="go"),
        queue,
    )

    updates = _artifact_updates(queue)
    message_ids = [_artifact_stream_meta(ev).get("message_id") for ev in updates]
    assert "msg-a" in message_ids
    assert "msg-b" in message_ids

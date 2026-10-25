from unittest.mock import AsyncMock

import pytest

from opencode_a2a.execution.stream_events import normalize_v1_event
from opencode_a2a.opencode_upstream_client import OpencodeMessage, OpencodeUpstreamClient
from opencode_a2a.upstream.contracts import (
    ExecutionState,
    UpdateKind,
)
from tests.support.settings import make_settings


def test_v1_event_decoder_returns_correlated_protocol_neutral_part_update() -> None:
    update = normalize_v1_event(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses-1",
                "messageID": "msg-1",
                "part": {
                    "id": "prt-1",
                    "type": "text",
                    "role": "assistant",
                    "text": "answer",
                    "sessionID": "ses-1",
                    "messageID": "msg-1",
                },
            },
        }
    )

    assert update is not None
    assert update.kind == UpdateKind.PART_UPDATED
    assert update.session_id == "ses-1"
    assert update.message_id == "msg-1"
    assert update.part_id == "prt-1"
    assert update.part_type == "text"
    assert update.role == "agent"
    assert update.snapshot == "answer"


@pytest.mark.asyncio
async def test_submit_prompt_separates_v1_receipt_from_terminal_outcome(monkeypatch) -> None:
    client = OpencodeUpstreamClient(make_settings(test_bearer_token="t-1"))
    message = OpencodeMessage(
        text="completed output",
        session_id="ses-1",
        message_id="assistant-msg-1",
        raw={"info": {"tokens": {"input": 2, "output": 3}}, "parts": []},
    )
    send_message = AsyncMock(return_value=message)
    monkeypatch.setattr(client, "send_message", send_message)

    submission = await client.submit_prompt("ses-1", "input")

    assert submission.receipt.session.id == "ses-1"
    assert submission.receipt.message_id is None
    assert submission.outcome.state == ExecutionState.COMPLETED
    assert submission.outcome.message_id == "assistant-msg-1"
    assert submission.outcome.output == "completed output"
    assert submission.outcome.usage == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert submission.outcome.response is not None
    assert submission.outcome.response["parts"] == []
    assert send_message.await_args is not None
    assert send_message.await_args.args == ("ses-1", "input")
    assert send_message.await_args.kwargs["parts"] is None
    assert "timeout_override" in send_message.await_args.kwargs
    await client.close()


@pytest.mark.asyncio
async def test_submit_prompt_preserves_terminal_v1_error_in_outcome(monkeypatch) -> None:
    client = OpencodeUpstreamClient(make_settings(test_bearer_token="t-1"))
    message = OpencodeMessage(
        text="",
        session_id="ses-1",
        message_id="assistant-msg-1",
        raw={
            "info": {
                "error": {
                    "name": "APIError",
                    "data": {"message": "upstream denied", "statusCode": 400},
                }
            }
        },
    )
    monkeypatch.setattr(client, "send_message", AsyncMock(return_value=message))

    submission = await client.submit_prompt("ses-1", "input")

    assert submission.outcome.state == ExecutionState.FAILED
    assert submission.outcome.error_state == ExecutionState.FAILED
    assert submission.outcome.error_type == "UPSTREAM_BAD_REQUEST"
    assert submission.outcome.upstream_status == 400
    assert "upstream denied" in (submission.outcome.error_message or "")
    await client.close()


@pytest.mark.asyncio
async def test_stream_updates_exposes_normalized_values_not_v1_event_mapping(monkeypatch) -> None:
    client = OpencodeUpstreamClient(make_settings(test_bearer_token="t-1"))

    async def stream_events(*_args, **_kwargs):
        yield {
            "type": "message.part.delta",
            "properties": {
                "sessionID": "ses-1",
                "messageID": "msg-1",
                "partID": "prt-1",
                "field": "text",
                "delta": "hello",
            },
        }

    monkeypatch.setattr(client, "stream_events", stream_events)

    updates = [update async for update in client.stream_updates()]

    assert len(updates) == 1
    assert updates[0].kind == UpdateKind.PART_DELTA
    assert updates[0].session_id == "ses-1"
    assert updates[0].part_id == "prt-1"
    assert updates[0].delta == "hello"
    assert not hasattr(updates[0], "properties")
    await client.close()

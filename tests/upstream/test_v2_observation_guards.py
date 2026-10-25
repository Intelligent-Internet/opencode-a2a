import asyncio

import pytest

from opencode_a2a.upstream.v2_events import V2ExecutionUncertainError
from tests.upstream.test_v2_execution import NativeServer


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["duplicate-delta", "unary-intervention"])
async def test_uncertain_progress_and_unsupported_intervention_never_complete(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    server = NativeServer(monkeypatch, "waiting")
    pending = asyncio.create_task(server.engine.submit("ses_one", "input", emit_updates=False))
    try:
        await asyncio.wait_for(server.admitted.wait(), 1)
        identity = server.inputs[0]
        for event in [
            server.event(
                "ses_one", "session.inbox.enqueued", inboxID=identity, item={"type": "user"}
            ),
            server.event("ses_one", "session.inbox.delivered", inboxID=identity),
            server.event("ses_one", "session.step.started", assistantMessageID="msg_assistant"),
        ]:
            server.feed.send(event)
        if scenario == "duplicate-delta":
            delta = {
                "id": "evt_repeated_delta",
                "type": "session.text.delta",
                "data": {
                    "sessionID": "ses_one",
                    "assistantMessageID": "msg_assistant",
                    "ordinal": 0,
                    "delta": "text",
                },
            }
            server.feed.send(delta)
            server.feed.send(delta)
            expected = "identity budget"
        else:
            server.feed.send(
                {
                    "id": "evt_form",
                    "type": "form.created",
                    "data": {"form": {"sessionID": "ses_one", "id": "frm_question"}},
                }
            )
            expected = "Unary interventions are unsupported"
        with pytest.raises(V2ExecutionUncertainError, match=expected):
            await asyncio.wait_for(pending, 1)
        assert len(server.inputs) == 1
        assert server.engine.sessions["ses_one"].subscription.error is not None
        assert server.engine._active == 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await server.close()

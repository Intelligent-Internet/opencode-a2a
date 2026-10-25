import asyncio

import pytest

from tests.execution.test_v2_executor_cancellation import ExecutorServer


@pytest.mark.asyncio
async def test_typed_multiselect_metadata_survives_shared_rendering_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ExecutorServer(monkeypatch)
    pending = None
    updates = None
    try:
        session = await server.client.create_session()
        pending = asyncio.create_task(
            server.client.submit_prompt(session, "Ask a question", stream_updates_requested=True)
        )
        await asyncio.wait_for(server.admitted.wait(), 1)
        identity = server.inputs[0]
        for event in [
            server.event(
                session, "session.inbox.enqueued", inboxID=identity, item={"type": "user"}
            ),
            server.event(session, "session.inbox.delivered", inboxID=identity),
            server.event(session, "session.step.started", assistantMessageID="msg_assistant"),
            {
                "id": "evt_form_multiple",
                "type": "form.created",
                "data": {
                    "form": {
                        "id": "frm_multiple",
                        "sessionID": session,
                        "metadata": {
                            "kind": "question",
                            "tool": {"messageID": "msg_assistant", "id": "tool_question"},
                        },
                        "fields": [
                            {
                                "key": "q0",
                                "type": "multiselect",
                                "title": "Pick",
                                "options": [{"value": "Alpha", "label": "Alpha"}],
                                "custom": False,
                            }
                        ],
                    }
                },
            },
        ]:
            server.feed.send(event)
        updates = server.client.stream_updates(session_id=session)
        update = await asyncio.wait_for(anext(updates), 1)
        assert update.intervention is not None
        assert update.intervention.request_id == "frm_multiple"
        questions = update.intervention.details["questions"]
        assert questions[0]["multiple"] is True
        assert questions[0]["custom"] is False
        assert questions[0]["options"] == [{"value": "Alpha", "label": "Alpha"}]
    finally:
        if updates is not None:
            await updates.aclose()
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await server.close()

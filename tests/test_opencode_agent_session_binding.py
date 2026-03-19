import asyncio

import pytest
from a2a.types import Task

from opencode_a2a_server.agent import OpencodeAgentExecutor
from opencode_a2a_server.opencode_client import OpencodeMessage
from tests.helpers import DummyChatOpencodeClient, DummyEventQueue, make_request_context


@pytest.mark.asyncio
async def test_agent_prefers_metadata_shared_session_id() -> None:
    client = DummyChatOpencodeClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = make_request_context(
        task_id="t-1",
        context_id="c-1",
        text="hello",
        metadata={"shared": {"session": {"id": "ses-bound"}}},
    )
    await executor.execute(ctx, q)

    assert client.created_sessions == 0
    assert client.sent_session_ids == ["ses-bound"]


@pytest.mark.asyncio
async def test_agent_passes_shared_model_override_to_upstream() -> None:
    client = DummyChatOpencodeClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = make_request_context(
        task_id="t-model",
        context_id="c-model",
        text="hello",
        metadata={"shared": {"model": {"providerID": "google", "modelID": "gemini-2.5-flash"}}},
    )
    await executor.execute(ctx, q)

    assert client.sent_model_overrides == [{"providerID": "google", "modelID": "gemini-2.5-flash"}]


@pytest.mark.asyncio
async def test_agent_ignores_partial_shared_model_override() -> None:
    client = DummyChatOpencodeClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = make_request_context(
        task_id="t-model-invalid",
        context_id="c-model-invalid",
        text="hello",
        metadata={"shared": {"model": {"providerID": "google"}}},
    )
    await executor.execute(ctx, q)

    assert client.sent_model_overrides == [None]


@pytest.mark.asyncio
async def test_agent_caches_bound_session_id_for_followup_requests() -> None:
    client = DummyChatOpencodeClient()
    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=False,
        session_cache_ttl_seconds=3600,
        session_cache_maxsize=100,
    )
    q = DummyEventQueue()

    ctx1 = make_request_context(
        task_id="t-1",
        context_id="c-1",
        text="hello",
        metadata={"shared": {"session": {"id": "ses-bound"}}},
    )
    await executor.execute(ctx1, q)

    ctx2 = make_request_context(
        task_id="t-2",
        context_id="c-1",
        text="follow",
        metadata=None,
    )
    await executor.execute(ctx2, q)

    assert client.created_sessions == 0
    assert client.sent_session_ids == ["ses-bound", "ses-bound"]


@pytest.mark.asyncio
async def test_agent_dedupes_concurrent_session_creates_per_context() -> None:
    class SlowCreateClient(DummyChatOpencodeClient):
        async def create_session(
            self,
            title: str | None = None,
            *,
            directory: str | None = None,
        ) -> str:
            await asyncio.sleep(0.05)
            return await super().create_session(title=title, directory=directory)

    client = SlowCreateClient()
    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=False,
        session_cache_ttl_seconds=3600,
        session_cache_maxsize=100,
    )

    async def run_one(task_id: str) -> None:
        q = DummyEventQueue()
        ctx = make_request_context(task_id=task_id, context_id="c-1", text="hi", metadata=None)
        await executor.execute(ctx, q)

    await asyncio.gather(run_one("t-1"), run_one("t-2"), run_one("t-3"))

    assert client.created_sessions == 1


@pytest.mark.asyncio
async def test_agent_uses_stable_fallback_message_id_when_upstream_missing_message_id() -> None:
    class MissingMessageIdClient(DummyChatOpencodeClient):
        async def send_message(
            self,
            session_id: str,
            text: str,
            *,
            directory: str | None = None,
            model_override: dict[str, str] | None = None,
            timeout_override=None,  # noqa: ANN001
        ) -> OpencodeMessage:
            del text, directory, model_override, timeout_override
            self.sent_session_ids.append(session_id)
            return OpencodeMessage(
                text="echo:hello",
                session_id=session_id,
                message_id=None,
                raw={},
            )

    client = MissingMessageIdClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="t-fallback", context_id="c-fallback", text="hello"),
        q,
    )

    task = next(event for event in q.events if isinstance(event, Task))
    assert "message_id" not in task.metadata["shared"]["session"]
    assert task.status.message.message_id == "t-fallback:c-fallback:assistant"


@pytest.mark.asyncio
async def test_agent_includes_usage_in_non_stream_task_metadata() -> None:
    class UsageClient(DummyChatOpencodeClient):
        async def send_message(
            self,
            session_id: str,
            text: str,
            *,
            directory: str | None = None,
            model_override: dict[str, str] | None = None,
            timeout_override=None,  # noqa: ANN001
        ) -> OpencodeMessage:
            del text, directory, model_override, timeout_override
            self.sent_session_ids.append(session_id)
            return OpencodeMessage(
                text="echo:hello",
                session_id=session_id,
                message_id="msg-usage",
                raw={
                    "info": {
                        "tokens": {
                            "input": 7,
                            "output": 3,
                            "reasoning": 0,
                            "cache": {"read": 0, "write": 0},
                        },
                        "cost": 0.0007,
                    }
                },
            )

    client = UsageClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="t-usage", context_id="c-usage", text="hello"),
        q,
    )

    task = next(event for event in q.events if isinstance(event, Task))
    usage = task.metadata["shared"]["usage"]
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 10
    assert "raw" not in usage

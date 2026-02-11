import asyncio

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.types import Message, MessageSendParams, Role, TextPart

from opencode_a2a.agent import OpencodeAgentExecutor
from opencode_a2a.opencode_client import OpencodeMessage


class DummyEventQueue:
    def __init__(self) -> None:
        self.events = []
        self.closed = False

    async def enqueue_event(self, event) -> None:  # noqa: ANN001
        self.events.append(event)

    async def close(self) -> None:
        self.closed = True


class DummyOpencodeClient:
    def __init__(self) -> None:
        self.created_sessions = 0
        self.sent_session_ids: list[str] = []
        self.stream_timeout = None
        self.directory = None
        from opencode_a2a.config import Settings

        self.settings = Settings(
            A2A_BEARER_TOKEN="test",
            OPENCODE_BASE_URL="http://localhost",
        )

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        self.created_sessions += 1
        return f"ses-created-{self.created_sessions}"

    async def send_message(
        self, session_id: str, text: str, *, directory: str | None = None, timeout_override=None
    ) -> OpencodeMessage:  # noqa: ANN001
        self.sent_session_ids.append(session_id)
        return OpencodeMessage(
            text=f"echo:{text}",
            session_id=session_id,
            message_id="m-1",
            raw={},
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        if False:
            yield {}


def _context(*, task_id: str, context_id: str, text: str, metadata: dict | None) -> RequestContext:
    msg = Message(
        message_id="msg-1",
        role=Role.user,
        parts=[TextPart(text=text)],
    )
    params = MessageSendParams(message=msg, metadata=metadata)
    return RequestContext(request=params, task_id=task_id, context_id=context_id)


@pytest.mark.asyncio
async def test_agent_prefers_metadata_opencode_session_id() -> None:
    client = DummyOpencodeClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = _context(
        task_id="t-1",
        context_id="c-1",
        text="hello",
        metadata={"opencode_session_id": "ses-bound"},
    )
    await executor.execute(ctx, q)

    assert client.created_sessions == 0
    assert client.sent_session_ids == ["ses-bound"]


@pytest.mark.asyncio
async def test_agent_caches_bound_session_id_for_followup_requests() -> None:
    client = DummyOpencodeClient()
    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=False,
        session_cache_ttl_seconds=3600,
        session_cache_maxsize=100,
    )
    q = DummyEventQueue()

    ctx1 = _context(
        task_id="t-1",
        context_id="c-1",
        text="hello",
        metadata={"opencode_session_id": "ses-bound"},
    )
    await executor.execute(ctx1, q)

    ctx2 = _context(
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
    class SlowCreateClient(DummyOpencodeClient):
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
        ctx = _context(task_id=task_id, context_id="c-1", text="hi", metadata=None)
        await executor.execute(ctx, q)

    await asyncio.gather(run_one("t-1"), run_one("t-2"), run_one("t-3"))

    assert client.created_sessions == 1

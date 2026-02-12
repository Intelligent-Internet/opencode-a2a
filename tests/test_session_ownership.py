import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import EventQueue

from opencode_a2a_serve.agent import OpencodeAgentExecutor, _TTLCache
from opencode_a2a_serve.config import Settings
from opencode_a2a_serve.opencode_client import OpencodeClient


@pytest.fixture
def mock_client():
    client = AsyncMock(spec=OpencodeClient)
    # Define sessions to return
    sessions = ["session-1", "session-2", "session-3"]
    current_idx = 0

    async def side_effect(title=None, directory=None):
        nonlocal current_idx
        res = sessions[current_idx]
        current_idx += 1
        return res

    client.create_session.side_effect = side_effect
    # Mock response for send_message
    response = MagicMock()
    response.text = "OpenCode response"
    response.session_id = "session-1"
    response.message_id = "msg-1"
    client.send_message.return_value = response

    # Use PropertyMock for properties
    type(client).directory = PropertyMock(return_value="/tmp/workspace")
    type(client).settings = PropertyMock(
        return_value=Settings(
            A2A_BEARER_TOKEN="test",
            OPENCODE_BASE_URL="http://localhost",
            A2A_ALLOW_DIRECTORY_OVERRIDE=True,
        )
    )
    return client


@pytest.mark.asyncio
async def test_identity_isolation(mock_client):
    executor = OpencodeAgentExecutor(mock_client, streaming_enabled=False)
    event_queue = AsyncMock(spec=EventQueue)

    # User 1, Context A
    context1 = MagicMock(spec=RequestContext)
    context1.task_id = "task-1"
    context1.context_id = "context-A"
    context1.call_context = MagicMock(spec=ServerCallContext)
    context1.call_context.state = {"identity": "user-1"}
    context1.get_user_input.return_value = "hello"
    context1.current_task = None
    context1.message = None
    context1.metadata = None

    await executor.execute(context1, event_queue)
    mock_client.create_session.assert_called_once()
    assert executor._sessions.get(("user-1", "context-A")) == "session-1"

    # User 2, Context A (Same context ID, different user)
    context2 = MagicMock(spec=RequestContext)
    context2.task_id = "task-2"
    context2.context_id = "context-A"
    context2.call_context = MagicMock(spec=ServerCallContext)
    context2.call_context.state = {"identity": "user-2"}
    context2.get_user_input.return_value = "hello"
    context2.current_task = None
    context2.message = None
    context2.metadata = None

    await executor.execute(context2, event_queue)
    # Should create a NEW session for user-2
    assert mock_client.create_session.call_count == 2
    assert executor._sessions.get(("user-2", "context-A")) == "session-2"
    # User 1's session should still be there
    assert executor._sessions.get(("user-1", "context-A")) == "session-1"


@pytest.mark.asyncio
async def test_session_hijack_prevention(mock_client):
    executor = OpencodeAgentExecutor(mock_client, streaming_enabled=False)
    event_queue = AsyncMock(spec=EventQueue)

    # User 1 creates session-1
    context1 = MagicMock(spec=RequestContext)
    context1.task_id = "task-1"
    context1.context_id = "context-A"
    context1.call_context = MagicMock(spec=ServerCallContext)
    context1.call_context.state = {"identity": "user-1"}
    context1.get_user_input.return_value = "hello"
    context1.current_task = None
    context1.message = None
    context1.metadata = None

    await executor.execute(context1, event_queue)
    assert executor._session_owners.get("session-1") == "user-1"

    # User 2 tries to bind to session-1 via metadata
    context2 = MagicMock(spec=RequestContext)
    context2.task_id = "task-2"
    context2.context_id = "context-B"
    context2.call_context = MagicMock(spec=ServerCallContext)
    context2.call_context.state = {"identity": "user-2"}
    context2.get_user_input.return_value = "hello"
    context2.metadata = {"opencode_session_id": "session-1"}
    context2.message = None

    # This should fail and emit an error
    await executor.execute(context2, event_queue)

    # Verify error emission
    # Note: we check call_args_list to find the Task
    from a2a.types import Task

    found_error_task = False
    for call in event_queue.enqueue_event.call_args_list:
        event = call[0][0]
        if isinstance(event, Task) and event.status.state.name == "failed":
            # Handle a2a types where parts contain root models
            part = event.status.message.parts[0]
            text = getattr(part, "text", None) or getattr(part.root, "text", "")
            if "not owned by you" in text:
                found_error_task = True
                break
    assert found_error_task


@pytest.mark.asyncio
async def test_concurrent_session_create_isolated_by_identity():
    client = AsyncMock(spec=OpencodeClient)
    created = 0

    async def create_session(title=None, directory=None):
        nonlocal created
        await asyncio.sleep(0.05)
        created += 1
        return f"session-{created}"

    async def send_message(
        session_id,
        _text,
        *,
        directory=None,
        timeout_override=None,
    ):
        del directory, timeout_override
        response = MagicMock()
        response.text = "OpenCode response"
        response.session_id = session_id
        response.message_id = "msg-1"
        return response

    client.create_session.side_effect = create_session
    client.send_message.side_effect = send_message
    type(client).directory = PropertyMock(return_value="/tmp/workspace")
    type(client).settings = PropertyMock(
        return_value=Settings(
            A2A_BEARER_TOKEN="test",
            OPENCODE_BASE_URL="http://localhost",
            A2A_ALLOW_DIRECTORY_OVERRIDE=True,
        )
    )

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    event_queue_1 = AsyncMock(spec=EventQueue)
    event_queue_2 = AsyncMock(spec=EventQueue)

    def _context(task_id: str, identity: str) -> RequestContext:
        context = MagicMock(spec=RequestContext)
        context.task_id = task_id
        context.context_id = "context-A"
        context.call_context = MagicMock(spec=ServerCallContext)
        context.call_context.state = {"identity": identity}
        context.get_user_input.return_value = "hello"
        context.current_task = None
        context.message = None
        context.metadata = None
        return context

    await asyncio.gather(
        executor.execute(_context("task-1", "user-1"), event_queue_1),
        executor.execute(_context("task-2", "user-2"), event_queue_2),
    )

    assert client.create_session.call_count == 2
    assert executor._sessions.get(("user-1", "context-A")) == "session-1"
    assert executor._sessions.get(("user-2", "context-A")) == "session-2"


def test_session_owner_cache_is_bounded():
    executor = OpencodeAgentExecutor(
        AsyncMock(spec=OpencodeClient),
        streaming_enabled=False,
        session_cache_ttl_seconds=3600,
        session_cache_maxsize=2,
    )

    executor._session_owners.set("session-1", "user-1")
    executor._session_owners.set("session-2", "user-2")
    executor._session_owners.set("session-3", "user-3")

    # Cache is bounded by maxsize and should not grow unbounded.
    assert len(executor._session_owners._store) <= 2


def test_owner_cache_refresh_on_get_extends_ttl():
    now = 0.0

    def _now() -> float:
        return now

    cache = _TTLCache(ttl_seconds=10, maxsize=10, now=_now, refresh_on_get=True)
    cache.set("session-1", "user-1")

    now = 9.0
    assert cache.get("session-1") == "user-1"

    now = 15.0
    assert cache.get("session-1") == "user-1"

    now = 26.0
    assert cache.get("session-1") is None


def test_owner_cache_evicts_earliest_expiring_entry_on_overflow():
    now = 0.0

    def _now() -> float:
        return now

    cache = _TTLCache(ttl_seconds=10, maxsize=2, now=_now, refresh_on_get=False)
    cache.set("session-1", "user-1")

    now = 2.0
    cache.set("session-2", "user-2")

    now = 4.0
    cache.set("session-3", "user-3")

    assert cache.get("session-1") is None
    assert cache.get("session-2") == "user-2"
    assert cache.get("session-3") == "user-3"


@pytest.mark.asyncio
async def test_preferred_session_claim_is_released_on_upstream_failure():
    client = AsyncMock(spec=OpencodeClient)

    async def send_message(
        session_id,
        _text,
        *,
        directory=None,  # noqa: ARG001
        timeout_override=None,  # noqa: ARG001
    ):
        raise RuntimeError(f"upstream failed for {session_id}")

    client.send_message.side_effect = send_message
    type(client).directory = PropertyMock(return_value="/tmp/workspace")
    type(client).settings = PropertyMock(
        return_value=Settings(
            A2A_BEARER_TOKEN="test",
            OPENCODE_BASE_URL="http://localhost",
            A2A_ALLOW_DIRECTORY_OVERRIDE=True,
        )
    )

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    event_queue = AsyncMock(spec=EventQueue)

    context = MagicMock(spec=RequestContext)
    context.task_id = "task-1"
    context.context_id = "context-A"
    context.call_context = MagicMock(spec=ServerCallContext)
    context.call_context.state = {"identity": "user-1"}
    context.get_user_input.return_value = "hello"
    context.metadata = {"opencode_session_id": "session-X"}
    context.current_task = None
    context.message = None

    await executor.execute(context, event_queue)

    assert executor._session_owners.get("session-X") is None
    assert executor._pending_session_claims.get("session-X") is None


@pytest.mark.asyncio
async def test_preferred_session_claim_is_released_on_upstream_cancellation():
    client = AsyncMock(spec=OpencodeClient)

    async def send_message(
        _session_id,
        _text,
        *,
        directory=None,  # noqa: ARG001
        timeout_override=None,  # noqa: ARG001
    ):
        raise asyncio.CancelledError()

    client.send_message.side_effect = send_message
    type(client).directory = PropertyMock(return_value="/tmp/workspace")
    type(client).settings = PropertyMock(
        return_value=Settings(
            A2A_BEARER_TOKEN="test",
            OPENCODE_BASE_URL="http://localhost",
            A2A_ALLOW_DIRECTORY_OVERRIDE=True,
        )
    )

    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    event_queue = AsyncMock(spec=EventQueue)

    context = MagicMock(spec=RequestContext)
    context.task_id = "task-1"
    context.context_id = "context-A"
    context.call_context = MagicMock(spec=ServerCallContext)
    context.call_context.state = {"identity": "user-1"}
    context.get_user_input.return_value = "hello"
    context.metadata = {"opencode_session_id": "session-X"}
    context.current_task = None
    context.message = None

    with pytest.raises(asyncio.CancelledError):
        await executor.execute(context, event_queue)

    assert executor._session_owners.get("session-X") is None
    assert executor._pending_session_claims.get("session-X") is None


@pytest.mark.asyncio
async def test_pending_preferred_session_claim_blocks_other_identity():
    executor = OpencodeAgentExecutor(AsyncMock(spec=OpencodeClient), streaming_enabled=False)

    session_id, pending = await executor._get_or_create_session(
        "user-1",
        "context-A",
        "hello",
        preferred_session_id="session-X",
    )
    assert session_id == "session-X"
    assert pending is True

    with pytest.raises(PermissionError, match="not owned by you"):
        await executor._get_or_create_session(
            "user-2",
            "context-B",
            "hello",
            preferred_session_id="session-X",
        )

    await executor._release_preferred_session_claim(identity="user-1", session_id="session-X")

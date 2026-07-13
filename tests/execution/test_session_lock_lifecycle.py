import asyncio
import gc
import weakref
from unittest.mock import AsyncMock

import pytest

from opencode_a2a.execution.session_manager import SessionManager
from opencode_a2a.opencode_upstream_client import OpencodeUpstreamClient
from opencode_a2a.server.state_store import SessionStateRepository


@pytest.mark.asyncio
async def test_session_manager_reuses_live_lock_for_same_session() -> None:
    manager = SessionManager(client=AsyncMock(spec=OpencodeUpstreamClient))

    lock1 = await manager.get_session_lock("session-1")
    lock2 = await manager.get_session_lock("session-1")

    assert lock1 is lock2


@pytest.mark.asyncio
async def test_session_manager_does_not_strongly_retain_idle_locks() -> None:
    manager = SessionManager(client=AsyncMock(spec=OpencodeUpstreamClient))

    lock = await manager.get_session_lock("session-1")
    lock_ref = weakref.ref(lock)
    assert "session-1" in manager._session_locks

    del lock
    gc.collect()

    assert lock_ref() is None
    assert "session-1" not in manager._session_locks


@pytest.mark.asyncio
async def test_session_manager_does_not_serialize_repository_reads_across_contexts() -> None:
    repository = AsyncMock(spec=SessionStateRepository)
    both_reads_started = asyncio.Event()
    release_reads = asyncio.Event()
    reads_started = 0

    async def get_session(*, identity: str, context_id: str) -> None:
        nonlocal reads_started
        del identity, context_id
        reads_started += 1
        if reads_started == 2:
            both_reads_started.set()
        await release_reads.wait()
        return None

    repository.get_session.side_effect = get_session
    repository.get_owner.return_value = None
    client = AsyncMock(spec=OpencodeUpstreamClient)
    client.create_session.side_effect = ["session-1", "session-2"]
    manager = SessionManager(client=client, state_repository=repository)

    requests = [
        asyncio.create_task(manager.get_or_create_session("user", "context-1", "first")),
        asyncio.create_task(manager.get_or_create_session("user", "context-2", "second")),
    ]
    await asyncio.wait_for(both_reads_started.wait(), timeout=1.0)
    release_reads.set()
    await asyncio.gather(*requests)

    assert reads_started == 2

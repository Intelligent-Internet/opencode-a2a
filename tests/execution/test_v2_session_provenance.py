import pytest

from tests.execution.test_v2_executor_cancellation import ExecutorServer


@pytest.mark.asyncio
async def test_registered_session_cannot_be_claimed_before_creator_ownership_is_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ExecutorServer(monkeypatch)
    try:
        session = await server.client.create_session()
        manager = server.executor._session_manager
        repository = manager._state_repository
        assert session in server.engine.sessions
        assert await repository.get_owner(session_id=session) is None
        before = len(server.requests)
        with pytest.raises(PermissionError, match="existing owner binding"):
            await manager.get_or_create_session(
                "intruder", "foreign-context", "must not run", preferred_session_id=session
            )
        assert len(server.requests) == before
        assert await repository.get_pending_claim(session_id=session) is None
        await repository.set_owner(session_id=session, identity="owner")
        actual, pending = await manager.get_or_create_session(
            "owner", "owner-context", "continue", preferred_session_id=session
        )
        assert actual == session and pending is False
        with pytest.raises(PermissionError, match="not owned"):
            await manager.get_or_create_session(
                "intruder", "another-context", "must not run", preferred_session_id=session
            )
        assert await repository.get_owner(session_id=session) == "owner"
        assert server.inputs == []
    finally:
        await server.close()

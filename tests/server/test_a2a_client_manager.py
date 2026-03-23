from __future__ import annotations

from types import SimpleNamespace

import pytest

from opencode_a2a.server import application as app_module


def _make_settings(**overrides: object) -> SimpleNamespace:
    values = {
        "a2a_client_timeout_seconds": 30.0,
        "a2a_client_card_fetch_timeout_seconds": 5.0,
        "a2a_client_use_client_preference": False,
        "a2a_client_bearer_token": None,
        "a2a_client_supported_transports": ("JSONRPC", "HTTP+JSON"),
        "a2a_client_cache_ttl_seconds": 60.0,
        "a2a_client_cache_maxsize": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_client_manager_evicts_lru_idle_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakeClient] = []

    class _FakeClient:
        def __init__(self, agent_url: str, *, settings) -> None:
            del settings
            self.agent_url = agent_url
            self.closed = False
            self.busy = False
            created.append(self)

        def is_busy(self) -> bool:
            return self.busy

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(app_module, "A2AClient", _FakeClient)

    manager = app_module.A2AClientManager(_make_settings(a2a_client_cache_maxsize=2))

    async with manager.borrow_client("http://peer-1"):
        pass
    async with manager.borrow_client("http://peer-2"):
        pass
    async with manager.borrow_client("http://peer-3"):
        pass

    assert set(manager.clients) == {"http://peer-2", "http://peer-3"}
    assert created[0].closed is True
    assert created[1].closed is False
    assert created[2].closed is False


@pytest.mark.asyncio
async def test_client_manager_defers_busy_client_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakeClient] = []

    class _FakeClient:
        def __init__(self, agent_url: str, *, settings) -> None:
            del settings
            self.agent_url = agent_url
            self.closed = False
            self.busy = False
            created.append(self)

        def is_busy(self) -> bool:
            return self.busy

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(app_module, "A2AClient", _FakeClient)

    manager = app_module.A2AClientManager(_make_settings(a2a_client_cache_maxsize=1))

    async with manager.borrow_client("http://peer-1") as first_client:
        first_client.busy = True
        async with manager.borrow_client("http://peer-2"):
            pass
        assert set(manager.clients) == {"http://peer-1", "http://peer-2"}
        assert first_client.closed is False
        first_client.busy = False

    assert set(manager.clients) == {"http://peer-2"}
    assert created[0].closed is True
    assert created[1].closed is False


@pytest.mark.asyncio
async def test_client_manager_evicts_expired_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakeClient] = []

    class _FakeClient:
        def __init__(self, agent_url: str, *, settings) -> None:
            del settings
            self.agent_url = agent_url
            self.closed = False
            self.busy = False
            created.append(self)

        def is_busy(self) -> bool:
            return self.busy

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(app_module, "A2AClient", _FakeClient)

    now = 100.0
    manager = app_module.A2AClientManager(_make_settings(a2a_client_cache_ttl_seconds=10.0))
    manager._now = lambda: now

    async with manager.borrow_client("http://peer-1"):
        pass

    now = 111.0
    async with manager.borrow_client("http://peer-2"):
        pass

    assert set(manager.clients) == {"http://peer-2"}
    assert created[0].closed is True
    assert created[1].closed is False

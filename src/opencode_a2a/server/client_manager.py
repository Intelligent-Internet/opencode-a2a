from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from ..client import A2AClient


class A2AClientManager:
    def __init__(self, settings) -> None:  # noqa: ANN001
        import time

        from ..client.config import load_settings as load_client_settings

        self.client_settings = load_client_settings(
            {
                "A2A_CLIENT_TIMEOUT_SECONDS": settings.a2a_client_timeout_seconds,
                "A2A_CLIENT_CARD_FETCH_TIMEOUT_SECONDS": (
                    settings.a2a_client_card_fetch_timeout_seconds
                ),
                "A2A_CLIENT_USE_CLIENT_PREFERENCE": settings.a2a_client_use_client_preference,
                "A2A_CLIENT_BEARER_TOKEN": settings.a2a_client_bearer_token,
                "A2A_CLIENT_BASIC_AUTH": settings.a2a_client_basic_auth,
                "A2A_CLIENT_SUPPORTED_TRANSPORTS": settings.a2a_client_supported_transports,
            }
        )
        self._cache_ttl_seconds = float(settings.a2a_client_cache_ttl_seconds)
        self._cache_maxsize = int(settings.a2a_client_cache_maxsize)
        self._now = time.monotonic
        self.clients: dict[str, _ClientCacheEntry] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def borrow_client(self, agent_url: str):
        url = agent_url.rstrip("/")
        if self._cache_maxsize <= 0:
            client = A2AClient(url, settings=self.client_settings)
            try:
                yield client
            finally:
                await client.close()
            return

        to_close: list[A2AClient] = []
        async with self._lock:
            now = self._now()
            entry = self.clients.get(url)
            if entry is not None and entry.expires_at is not None and entry.expires_at <= now:
                if entry.borrow_count > 0 or entry.client.is_busy():
                    entry.pending_eviction = True
                else:
                    self.clients.pop(url, None)
                    to_close.append(entry.client)
                    entry = None
            to_close.extend(self._evict_locked(now=now, protected_keys={url}))
            if entry is None:
                entry = _ClientCacheEntry(
                    client=A2AClient(url, settings=self.client_settings),
                    last_used=now,
                    expires_at=None
                    if self._cache_ttl_seconds <= 0
                    else now + self._cache_ttl_seconds,
                )
                self.clients[url] = entry
            else:
                entry.last_used = now
                entry.expires_at = (
                    None if self._cache_ttl_seconds <= 0 else now + self._cache_ttl_seconds
                )
                entry.pending_eviction = False
            entry.borrow_count += 1
            to_close.extend(self._evict_locked(now=now, protected_keys={url}))
        await self._close_clients(to_close)

        try:
            yield entry.client
        finally:
            async with self._lock:
                now = self._now()
                current = self.clients.get(url)
                if current is entry:
                    if current.borrow_count > 0:
                        current.borrow_count -= 1
                    current.last_used = now
                    current.expires_at = (
                        None if self._cache_ttl_seconds <= 0 else now + self._cache_ttl_seconds
                    )
                to_close = self._evict_locked(now=now)
            await self._close_clients(to_close)

    async def close_all(self) -> None:
        async with self._lock:
            clients = [entry.client for entry in self.clients.values()]
            self.clients.clear()
        for client in clients:
            await client.close()

    def _evict_locked(
        self,
        *,
        now: float,
        protected_keys: set[str] | None = None,
    ) -> list[A2AClient]:
        protected = protected_keys or set()
        to_close: list[A2AClient] = []

        for key, entry in list(self.clients.items()):
            expired = entry.expires_at is not None and entry.expires_at <= now
            if not expired and not entry.pending_eviction:
                continue
            if key in protected or entry.borrow_count > 0 or entry.client.is_busy():
                entry.pending_eviction = True
                continue
            self.clients.pop(key, None)
            to_close.append(entry.client)

        if self._cache_maxsize <= 0 or len(self.clients) <= self._cache_maxsize:
            return to_close

        if any(entry.pending_eviction for entry in self.clients.values()):
            return to_close

        for key, entry in sorted(self.clients.items(), key=lambda item: item[1].last_used):
            if len(self.clients) <= self._cache_maxsize:
                break
            if key in protected:
                continue
            if entry.borrow_count > 0 or entry.client.is_busy():
                entry.pending_eviction = True
                continue
            self.clients.pop(key, None)
            to_close.append(entry.client)

        return to_close

    async def _close_clients(self, clients: list[A2AClient]) -> None:
        for client in clients:
            await client.close()


class _ClientCacheEntry:
    def __init__(
        self,
        *,
        client: A2AClient,
        last_used: float,
        expires_at: float | None,
        borrow_count: int = 0,
        pending_eviction: bool = False,
    ) -> None:
        self.client = client
        self.last_used = last_used
        self.expires_at = expires_at
        self.borrow_count = borrow_count
        self.pending_eviction = pending_eviction

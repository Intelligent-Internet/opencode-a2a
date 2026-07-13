from __future__ import annotations

import asyncio
import weakref
from typing import TypeVar

from ..server.state_store import MemorySessionStateRepository, SessionStateRepository

_LockKey = TypeVar("_LockKey")


class SessionManager:
    def __init__(
        self,
        *,
        client,
        session_cache_ttl_seconds: int = 3600,
        session_cache_maxsize: int = 10_000,
        pending_session_claim_ttl_seconds: float = 30.0,
        state_repository: SessionStateRepository | None = None,
    ) -> None:
        self._client = client
        self._state_repository = state_repository or MemorySessionStateRepository(
            ttl_seconds=session_cache_ttl_seconds,
            maxsize=session_cache_maxsize,
            pending_claim_ttl_seconds=pending_session_claim_ttl_seconds,
        )
        self._registry_lock = asyncio.Lock()
        self._inflight_session_creates: dict[tuple[str, str], asyncio.Task[str]] = {}
        self._context_locks: weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._ownership_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def _get_registered_lock(
        self,
        registry: weakref.WeakValueDictionary[_LockKey, asyncio.Lock],
        key: _LockKey,
    ) -> asyncio.Lock:
        async with self._registry_lock:
            lock = registry.get(key)
            if lock is None:
                lock = asyncio.Lock()
                registry[key] = lock
            return lock

    async def get_or_create_session(
        self,
        identity: str,
        context_id: str,
        title: str,
        *,
        preferred_session_id: str | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[str, bool]:
        if preferred_session_id:
            pending_claim = await self.claim_preferred_session(
                identity=identity,
                session_id=preferred_session_id,
            )
            if not pending_claim:
                cache_key = (identity, context_id)
                context_lock = await self._get_registered_lock(self._context_locks, cache_key)
                async with context_lock:
                    await self._state_repository.set_session(
                        identity=identity,
                        context_id=context_id,
                        session_id=preferred_session_id,
                    )
            return preferred_session_id, pending_claim

        task: asyncio.Task[str] | None = None
        cache_key = (identity, context_id)
        context_lock = await self._get_registered_lock(self._context_locks, cache_key)
        async with context_lock:
            existing = await self._state_repository.get_session(
                identity=cache_key[0],
                context_id=cache_key[1],
            )
            if existing:
                return existing, False
            async with self._registry_lock:
                task = self._inflight_session_creates.get(cache_key)
                if task is None:
                    create_session_kwargs: dict[str, str] = {}
                    if directory is not None:
                        create_session_kwargs["directory"] = directory
                    if workspace_id is not None:
                        create_session_kwargs["workspace_id"] = workspace_id
                    task = asyncio.create_task(
                        self._client.create_session(
                            title=title,
                            **create_session_kwargs,
                        )
                    )
                    self._inflight_session_creates[cache_key] = task

        try:
            session_id = await task
        except Exception:
            async with self._registry_lock:
                if self._inflight_session_creates.get(cache_key) is task:
                    self._inflight_session_creates.pop(cache_key, None)
            raise

        async with context_lock:
            ownership_lock = await self._get_registered_lock(self._ownership_locks, session_id)
            async with ownership_lock:
                owner = await self._state_repository.get_owner(session_id=session_id)
                if owner and owner != identity:
                    async with self._registry_lock:
                        if self._inflight_session_creates.get(cache_key) is task:
                            self._inflight_session_creates.pop(cache_key, None)
                    raise PermissionError(f"Session {session_id} is not owned by you")
                await self._state_repository.set_session(
                    identity=cache_key[0],
                    context_id=cache_key[1],
                    session_id=session_id,
                )
                if not owner:
                    await self._state_repository.set_owner(session_id=session_id, identity=identity)
        async with self._registry_lock:
            if self._inflight_session_creates.get(cache_key) is task:
                self._inflight_session_creates.pop(cache_key, None)
        return session_id, False

    async def finalize_preferred_session_binding(
        self,
        *,
        identity: str,
        context_id: str,
        session_id: str,
    ) -> None:
        await self.finalize_session_claim(identity=identity, session_id=session_id)
        cache_key = (identity, context_id)
        context_lock = await self._get_registered_lock(self._context_locks, cache_key)
        async with context_lock:
            await self._state_repository.set_session(
                identity=identity,
                context_id=context_id,
                session_id=session_id,
            )

    async def claim_preferred_session(self, *, identity: str, session_id: str) -> bool:
        ownership_lock = await self._get_registered_lock(self._ownership_locks, session_id)
        async with ownership_lock:
            owner = await self._state_repository.get_owner(session_id=session_id)
            pending_owner = await self._state_repository.get_pending_claim(session_id=session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if owner == identity:
                return False
            await self._state_repository.set_pending_claim(session_id=session_id, identity=identity)
            return True

    async def finalize_session_claim(self, *, identity: str, session_id: str) -> None:
        ownership_lock = await self._get_registered_lock(self._ownership_locks, session_id)
        async with ownership_lock:
            owner = await self._state_repository.get_owner(session_id=session_id)
            pending_owner = await self._state_repository.get_pending_claim(session_id=session_id)
            if owner and owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            if pending_owner and pending_owner != identity:
                raise PermissionError(f"Session {session_id} is not owned by you")
            await self._state_repository.set_owner(session_id=session_id, identity=identity)
            await self._state_repository.clear_pending_claim(
                session_id=session_id,
                identity=identity,
            )

    async def release_preferred_session_claim(self, *, identity: str, session_id: str) -> None:
        ownership_lock = await self._get_registered_lock(self._ownership_locks, session_id)
        async with ownership_lock:
            await self._state_repository.clear_pending_claim(
                session_id=session_id,
                identity=identity,
            )

    async def get_session_lock(self, session_id: str) -> asyncio.Lock:
        return await self._get_registered_lock(self._session_locks, session_id)

    async def pop_cached_session(
        self,
        *,
        identity: str,
        context_id: str,
    ) -> asyncio.Task[str] | None:
        cache_key = (identity, context_id)
        context_lock = await self._get_registered_lock(self._context_locks, cache_key)
        async with context_lock:
            await self._state_repository.pop_session(identity=identity, context_id=context_id)
            async with self._registry_lock:
                return self._inflight_session_creates.pop(cache_key, None)

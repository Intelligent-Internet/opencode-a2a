from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    and_,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..execution.stream_state import _TTLCache
from ..runtime_state import InterruptRequestBinding, InterruptRequestTombstone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_STATE_METADATA = MetaData()

_SESSION_BINDINGS = Table(
    "a2a_session_bindings",
    _STATE_METADATA,
    Column("identity", String, primary_key=True),
    Column("context_id", String, primary_key=True),
    Column("session_id", String, nullable=False),
)

_SESSION_OWNERS = Table(
    "a2a_session_owners",
    _STATE_METADATA,
    Column("session_id", String, primary_key=True),
    Column("identity", String, nullable=False),
)

_PENDING_SESSION_CLAIMS = Table(
    "a2a_pending_session_claims",
    _STATE_METADATA,
    Column("session_id", String, primary_key=True),
    Column("identity", String, nullable=False),
    Column("updated_at", Float, nullable=False),
)

_INTERRUPT_REQUESTS = Table(
    "a2a_interrupt_requests",
    _STATE_METADATA,
    Column("request_id", String, primary_key=True),
    Column("session_id", String, nullable=True),
    Column("interrupt_type", String, nullable=True),
    Column("identity", String, nullable=True),
    Column("task_id", String, nullable=True),
    Column("context_id", String, nullable=True),
    Column("details_json", String, nullable=True),
    Column("expires_at", Float, nullable=True),
    Column("tombstone_expires_at", Float, nullable=True),
)

_MEMORY_SESSION_BINDING_TTL_SECONDS = 3600
_MEMORY_SESSION_BINDING_MAXSIZE = 10_000


class SessionStateRepository(ABC):
    @abstractmethod
    async def get_session(self, *, identity: str, context_id: str) -> str | None: ...

    @abstractmethod
    async def set_session(self, *, identity: str, context_id: str, session_id: str) -> None: ...

    @abstractmethod
    async def pop_session(self, *, identity: str, context_id: str) -> None: ...

    @abstractmethod
    async def get_owner(self, *, session_id: str) -> str | None: ...

    @abstractmethod
    async def set_owner(self, *, session_id: str, identity: str) -> None: ...

    @abstractmethod
    async def get_pending_claim(self, *, session_id: str) -> str | None: ...

    @abstractmethod
    async def set_pending_claim(self, *, session_id: str, identity: str) -> None: ...

    @abstractmethod
    async def clear_pending_claim(
        self,
        *,
        session_id: str,
        identity: str | None = None,
    ) -> None: ...


class InterruptRequestRepository(ABC):
    @abstractmethod
    async def remember(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None,
        task_id: str | None,
        context_id: str | None,
        details: dict[str, Any] | None,
        ttl_seconds: float | None,
    ) -> None: ...

    @abstractmethod
    async def resolve(
        self,
        *,
        request_id: str,
    ) -> tuple[str, InterruptRequestBinding | None]: ...

    @abstractmethod
    async def discard(self, *, request_id: str) -> None: ...

    @abstractmethod
    async def list_pending(
        self,
        *,
        identity: str,
        interrupt_type: str | None = None,
    ) -> list[InterruptRequestBinding]: ...


class MemorySessionStateRepository(SessionStateRepository):
    def __init__(
        self,
        *,
        ttl_seconds: int,
        maxsize: int,
        pending_claim_ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions = _TTLCache(ttl_seconds=ttl_seconds, maxsize=maxsize)
        self.session_owners = _TTLCache(
            ttl_seconds=ttl_seconds,
            maxsize=maxsize,
            refresh_on_get=True,
        )
        self.pending_session_claims: dict[str, str] = {}
        self._pending_session_claim_expiries: dict[str, float] = {}
        self._pending_claim_ttl_seconds = float(pending_claim_ttl_seconds)
        self._clock = clock

    def _prune_pending_claims(self, *, now: float) -> None:
        expired = [
            session_id
            for session_id, expires_at in self._pending_session_claim_expiries.items()
            if expires_at <= now
        ]
        for session_id in expired:
            self.pending_session_claims.pop(session_id, None)
            self._pending_session_claim_expiries.pop(session_id, None)

    async def get_session(self, *, identity: str, context_id: str) -> str | None:
        return self.sessions.get((identity, context_id))

    async def set_session(self, *, identity: str, context_id: str, session_id: str) -> None:
        self.sessions.set((identity, context_id), session_id)

    async def pop_session(self, *, identity: str, context_id: str) -> None:
        self.sessions.pop((identity, context_id))

    async def get_owner(self, *, session_id: str) -> str | None:
        return self.session_owners.get(session_id)

    async def set_owner(self, *, session_id: str, identity: str) -> None:
        self.session_owners.set(session_id, identity)

    async def get_pending_claim(self, *, session_id: str) -> str | None:
        self._prune_pending_claims(now=self._clock())
        return self.pending_session_claims.get(session_id)

    async def set_pending_claim(self, *, session_id: str, identity: str) -> None:
        now = self._clock()
        self._prune_pending_claims(now=now)
        if self._pending_claim_ttl_seconds <= 0:
            await self.clear_pending_claim(session_id=session_id)
            return
        self.pending_session_claims[session_id] = identity
        self._pending_session_claim_expiries[session_id] = now + self._pending_claim_ttl_seconds

    async def clear_pending_claim(self, *, session_id: str, identity: str | None = None) -> None:
        if identity is None or self.pending_session_claims.get(session_id) == identity:
            self.pending_session_claims.pop(session_id, None)
            self._pending_session_claim_expiries.pop(session_id, None)


class DatabaseSessionStateRepository(SessionStateRepository):
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        pending_claim_ttl_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self._pending_claim_ttl_seconds = float(pending_claim_ttl_seconds)
        self._clock = clock
        self._initialized = False
        self._session_maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            await conn.run_sync(_STATE_METADATA.create_all)
        self._initialized = True

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _prune_expired_pending_claims(
        self,
        session: AsyncSession,
        *,
        now: float,
    ) -> None:
        if self._pending_claim_ttl_seconds <= 0:
            await session.execute(delete(_PENDING_SESSION_CLAIMS))
            return
        expires_before = now - self._pending_claim_ttl_seconds
        await session.execute(
            delete(_PENDING_SESSION_CLAIMS).where(
                _PENDING_SESSION_CLAIMS.c.updated_at <= expires_before
            )
        )

    async def get_session(self, *, identity: str, context_id: str) -> str | None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            result = await session.execute(
                select(_SESSION_BINDINGS.c.session_id).where(
                    and_(
                        _SESSION_BINDINGS.c.identity == identity,
                        _SESSION_BINDINGS.c.context_id == context_id,
                    )
                )
            )
            return cast("str | None", result.scalar_one_or_none())

    async def set_session(self, *, identity: str, context_id: str, session_id: str) -> None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            exists = await session.execute(
                select(_SESSION_BINDINGS.c.session_id).where(
                    and_(
                        _SESSION_BINDINGS.c.identity == identity,
                        _SESSION_BINDINGS.c.context_id == context_id,
                    )
                )
            )
            if exists.scalar_one_or_none() is None:
                await session.execute(
                    insert(_SESSION_BINDINGS).values(
                        identity=identity,
                        context_id=context_id,
                        session_id=session_id,
                    )
                )
            else:
                await session.execute(
                    update(_SESSION_BINDINGS)
                    .where(
                        and_(
                            _SESSION_BINDINGS.c.identity == identity,
                            _SESSION_BINDINGS.c.context_id == context_id,
                        )
                    )
                    .values(session_id=session_id)
                )

    async def pop_session(self, *, identity: str, context_id: str) -> None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            await session.execute(
                delete(_SESSION_BINDINGS).where(
                    and_(
                        _SESSION_BINDINGS.c.identity == identity,
                        _SESSION_BINDINGS.c.context_id == context_id,
                    )
                )
            )

    async def get_owner(self, *, session_id: str) -> str | None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            result = await session.execute(
                select(_SESSION_OWNERS.c.identity).where(_SESSION_OWNERS.c.session_id == session_id)
            )
            return cast("str | None", result.scalar_one_or_none())

    async def set_owner(self, *, session_id: str, identity: str) -> None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            exists = await session.execute(
                select(_SESSION_OWNERS.c.session_id).where(
                    _SESSION_OWNERS.c.session_id == session_id
                )
            )
            if exists.scalar_one_or_none() is None:
                await session.execute(
                    insert(_SESSION_OWNERS).values(
                        session_id=session_id,
                        identity=identity,
                    )
                )
            else:
                await session.execute(
                    update(_SESSION_OWNERS)
                    .where(_SESSION_OWNERS.c.session_id == session_id)
                    .values(identity=identity)
                )

    async def get_pending_claim(self, *, session_id: str) -> str | None:
        await self._ensure_initialized()
        now = self._clock()
        async with self._session_maker.begin() as session:
            await self._prune_expired_pending_claims(session, now=now)
            result = await session.execute(
                select(_PENDING_SESSION_CLAIMS.c.identity).where(
                    _PENDING_SESSION_CLAIMS.c.session_id == session_id
                )
            )
            return cast("str | None", result.scalar_one_or_none())

    async def set_pending_claim(self, *, session_id: str, identity: str) -> None:
        await self._ensure_initialized()
        now = self._clock()
        async with self._session_maker.begin() as session:
            await self._prune_expired_pending_claims(session, now=now)
            if self._pending_claim_ttl_seconds <= 0:
                await session.execute(
                    delete(_PENDING_SESSION_CLAIMS).where(
                        _PENDING_SESSION_CLAIMS.c.session_id == session_id
                    )
                )
                return
            exists = await session.execute(
                select(_PENDING_SESSION_CLAIMS.c.session_id).where(
                    _PENDING_SESSION_CLAIMS.c.session_id == session_id
                )
            )
            values = {"identity": identity, "updated_at": now}
            if exists.scalar_one_or_none() is None:
                await session.execute(
                    insert(_PENDING_SESSION_CLAIMS).values(session_id=session_id, **values)
                )
            else:
                await session.execute(
                    update(_PENDING_SESSION_CLAIMS)
                    .where(_PENDING_SESSION_CLAIMS.c.session_id == session_id)
                    .values(**values)
                )

    async def clear_pending_claim(
        self,
        *,
        session_id: str,
        identity: str | None = None,
    ) -> None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            stmt = delete(_PENDING_SESSION_CLAIMS).where(
                _PENDING_SESSION_CLAIMS.c.session_id == session_id
            )
            if identity is not None:
                stmt = stmt.where(_PENDING_SESSION_CLAIMS.c.identity == identity)
            await session.execute(stmt)


class MemoryInterruptRequestRepository(InterruptRequestRepository):
    def __init__(
        self,
        *,
        request_ttl_seconds: float,
        tombstone_ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._request_ttl_seconds = float(request_ttl_seconds)
        self._tombstone_ttl_seconds = float(tombstone_ttl_seconds)
        self._clock = clock
        self._interrupt_requests: dict[str, InterruptRequestBinding] = {}
        self._interrupt_request_tombstones: dict[str, InterruptRequestTombstone] = {}

    def _prune_interrupt_requests(self, *, now: float) -> None:
        expired = [
            request_id
            for request_id, binding in self._interrupt_requests.items()
            if binding.expires_at <= now
        ]
        for request_id in expired:
            self._interrupt_requests.pop(request_id, None)
            self._remember_interrupt_request_tombstone(request_id, now=now)

    def _prune_interrupt_request_tombstones(self, *, now: float) -> None:
        expired = [
            request_id
            for request_id, tombstone in self._interrupt_request_tombstones.items()
            if tombstone.expires_at <= now
        ]
        for request_id in expired:
            self._interrupt_request_tombstones.pop(request_id, None)

    def _remember_interrupt_request_tombstone(self, request_id: str, *, now: float) -> None:
        ttl = self._tombstone_ttl_seconds
        if ttl <= 0:
            self._interrupt_request_tombstones.pop(request_id, None)
            return
        self._interrupt_request_tombstones[request_id] = InterruptRequestTombstone(
            request_id=request_id,
            expires_at=now + ttl,
        )

    async def remember(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None,
        task_id: str | None,
        context_id: str | None,
        details: dict[str, Any] | None,
        ttl_seconds: float | None,
    ) -> None:
        now = self._clock()
        self._prune_interrupt_requests(now=now)
        self._prune_interrupt_request_tombstones(now=now)
        ttl = self._request_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._interrupt_requests[request_id] = InterruptRequestBinding(
            request_id=request_id,
            session_id=session_id,
            interrupt_type=interrupt_type,
            identity=identity,
            task_id=task_id,
            context_id=context_id,
            details=dict(details) if isinstance(details, dict) else None,
            expires_at=now + max(0.0, float(ttl)),
        )
        self._interrupt_request_tombstones.pop(request_id, None)

    async def resolve(
        self,
        *,
        request_id: str,
    ) -> tuple[str, InterruptRequestBinding | None]:
        if not request_id:
            return "missing", None
        now = self._clock()
        self._prune_interrupt_request_tombstones(now=now)
        binding = self._interrupt_requests.get(request_id)
        if binding is None:
            if request_id in self._interrupt_request_tombstones:
                return "expired", None
            return "missing", None
        if binding.expires_at <= now:
            self._interrupt_requests.pop(request_id, None)
            self._prune_interrupt_requests(now=now)
            self._remember_interrupt_request_tombstone(request_id, now=now)
            return "expired", None
        self._prune_interrupt_requests(now=now)
        return "active", binding

    async def discard(self, *, request_id: str) -> None:
        self._interrupt_requests.pop(request_id, None)
        self._interrupt_request_tombstones.pop(request_id, None)

    async def list_pending(
        self,
        *,
        identity: str,
        interrupt_type: str | None = None,
    ) -> list[InterruptRequestBinding]:
        now = self._clock()
        self._prune_interrupt_requests(now=now)
        self._prune_interrupt_request_tombstones(now=now)
        normalized_type = interrupt_type.strip() if isinstance(interrupt_type, str) else None
        items = [
            binding
            for binding in self._interrupt_requests.values()
            if binding.identity == identity
            and (normalized_type is None or binding.interrupt_type == normalized_type)
            and binding.expires_at > now
        ]
        return sorted(items, key=lambda item: (item.expires_at, item.request_id))


class DatabaseInterruptRequestRepository(InterruptRequestRepository):
    @staticmethod
    def _encode_details(details: dict[str, Any] | None) -> str | None:
        if not isinstance(details, dict):
            return None
        return json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_details(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        request_ttl_seconds: float,
        tombstone_ttl_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self._request_ttl_seconds = float(request_ttl_seconds)
        self._tombstone_ttl_seconds = float(tombstone_ttl_seconds)
        self._clock = clock
        self._initialized = False
        self._session_maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self.engine.begin() as conn:
            await conn.run_sync(_STATE_METADATA.create_all)
        self._initialized = True

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def _prune_tombstones(self, session: AsyncSession, *, now: float) -> None:
        await session.execute(
            delete(_INTERRUPT_REQUESTS).where(
                and_(
                    _INTERRUPT_REQUESTS.c.tombstone_expires_at.is_not(None),
                    _INTERRUPT_REQUESTS.c.tombstone_expires_at <= now,
                )
            )
        )

    async def _set_tombstone(self, session: AsyncSession, *, request_id: str, now: float) -> None:
        tombstone_expires_at = (
            None if self._tombstone_ttl_seconds <= 0 else now + self._tombstone_ttl_seconds
        )
        await session.execute(
            update(_INTERRUPT_REQUESTS)
            .where(_INTERRUPT_REQUESTS.c.request_id == request_id)
            .values(
                session_id=None,
                interrupt_type=None,
                identity=None,
                task_id=None,
                context_id=None,
                details_json=None,
                expires_at=None,
                tombstone_expires_at=tombstone_expires_at,
            )
        )

    async def remember(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None,
        task_id: str | None,
        context_id: str | None,
        details: dict[str, Any] | None,
        ttl_seconds: float | None,
    ) -> None:
        await self._ensure_initialized()
        now = self._clock()
        ttl = self._request_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = now + max(0.0, float(ttl))
        async with self._session_maker.begin() as session:
            await self._prune_tombstones(session, now=now)
            exists = await session.execute(
                select(_INTERRUPT_REQUESTS.c.request_id).where(
                    _INTERRUPT_REQUESTS.c.request_id == request_id
                )
            )
            values = {
                "session_id": session_id,
                "interrupt_type": interrupt_type,
                "identity": identity,
                "task_id": task_id,
                "context_id": context_id,
                "details_json": self._encode_details(details),
                "expires_at": expires_at,
                "tombstone_expires_at": None,
            }
            if exists.scalar_one_or_none() is None:
                await session.execute(
                    insert(_INTERRUPT_REQUESTS).values(request_id=request_id, **values)
                )
            else:
                await session.execute(
                    update(_INTERRUPT_REQUESTS)
                    .where(_INTERRUPT_REQUESTS.c.request_id == request_id)
                    .values(**values)
                )

    async def resolve(
        self,
        *,
        request_id: str,
    ) -> tuple[str, InterruptRequestBinding | None]:
        if not request_id:
            return "missing", None
        await self._ensure_initialized()
        now = self._clock()
        async with self._session_maker.begin() as session:
            await self._prune_tombstones(session, now=now)
            result = await session.execute(
                select(_INTERRUPT_REQUESTS).where(_INTERRUPT_REQUESTS.c.request_id == request_id)
            )
            row = result.mappings().one_or_none()
            if row is None:
                return "missing", None
            tombstone_expires_at = row.get("tombstone_expires_at")
            if tombstone_expires_at is not None and tombstone_expires_at > now:
                return "expired", None
            expires_at = row.get("expires_at")
            if expires_at is None:
                return "missing", None
            if expires_at <= now:
                await self._set_tombstone(session, request_id=request_id, now=now)
                return "expired", None
            return (
                "active",
                InterruptRequestBinding(
                    request_id=request_id,
                    session_id=cast("str", row["session_id"]),
                    interrupt_type=cast("str", row["interrupt_type"]),
                    identity=cast("str | None", row["identity"]),
                    task_id=cast("str | None", row["task_id"]),
                    context_id=cast("str | None", row["context_id"]),
                    details=self._decode_details(row.get("details_json")),
                    expires_at=cast("float", expires_at),
                ),
            )

    async def discard(self, *, request_id: str) -> None:
        await self._ensure_initialized()
        async with self._session_maker.begin() as session:
            await session.execute(
                delete(_INTERRUPT_REQUESTS).where(_INTERRUPT_REQUESTS.c.request_id == request_id)
            )

    async def list_pending(
        self,
        *,
        identity: str,
        interrupt_type: str | None = None,
    ) -> list[InterruptRequestBinding]:
        await self._ensure_initialized()
        now = self._clock()
        normalized_type = interrupt_type.strip() if isinstance(interrupt_type, str) else None
        async with self._session_maker.begin() as session:
            await self._prune_tombstones(session, now=now)
            stmt = (
                select(_INTERRUPT_REQUESTS)
                .where(
                    and_(
                        _INTERRUPT_REQUESTS.c.identity == identity,
                        _INTERRUPT_REQUESTS.c.expires_at.is_not(None),
                        _INTERRUPT_REQUESTS.c.expires_at > now,
                    )
                )
                .order_by(
                    _INTERRUPT_REQUESTS.c.expires_at.asc(),
                    _INTERRUPT_REQUESTS.c.request_id.asc(),
                )
            )
            if normalized_type is not None:
                stmt = stmt.where(_INTERRUPT_REQUESTS.c.interrupt_type == normalized_type)
            result = await session.execute(stmt)
            rows = result.mappings().all()
            return [
                InterruptRequestBinding(
                    request_id=cast("str", row["request_id"]),
                    session_id=cast("str", row["session_id"]),
                    interrupt_type=cast("str", row["interrupt_type"]),
                    identity=cast("str | None", row["identity"]),
                    task_id=cast("str | None", row["task_id"]),
                    context_id=cast("str | None", row["context_id"]),
                    details=self._decode_details(row.get("details_json")),
                    expires_at=cast("float", row["expires_at"]),
                )
                for row in rows
            ]


def build_session_state_repository(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
) -> SessionStateRepository:
    if settings.a2a_task_store_backend == "database":
        return DatabaseSessionStateRepository(
            engine=cast("AsyncEngine", engine),
            pending_claim_ttl_seconds=settings.a2a_pending_session_claim_ttl_seconds,
        )
    return MemorySessionStateRepository(
        ttl_seconds=_MEMORY_SESSION_BINDING_TTL_SECONDS,
        maxsize=_MEMORY_SESSION_BINDING_MAXSIZE,
        pending_claim_ttl_seconds=settings.a2a_pending_session_claim_ttl_seconds,
    )


def build_interrupt_request_repository(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
) -> InterruptRequestRepository:
    if settings.a2a_task_store_backend == "database":
        return DatabaseInterruptRequestRepository(
            engine=cast("AsyncEngine", engine),
            request_ttl_seconds=settings.a2a_interrupt_request_ttl_seconds,
            tombstone_ttl_seconds=settings.a2a_interrupt_request_tombstone_ttl_seconds,
        )
    return MemoryInterruptRequestRepository(
        request_ttl_seconds=settings.a2a_interrupt_request_ttl_seconds,
        tombstone_ttl_seconds=settings.a2a_interrupt_request_tombstone_ttl_seconds,
    )


async def initialize_state_repository(repository: object) -> None:
    initialize = getattr(repository, "initialize", None)
    if callable(initialize):
        await initialize()

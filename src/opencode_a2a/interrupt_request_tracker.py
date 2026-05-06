from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .runtime_state import InterruptRequestBinding


@dataclass(frozen=True)
class InterruptRequestResolution:
    status: Literal["active", "expired", "missing"]
    binding: InterruptRequestBinding | None


class BoundInterruptRequestTracker:
    def __init__(self, client: object) -> None:
        remember_request = getattr(client, "remember_interrupt_request", None)
        resolve_request = getattr(client, "resolve_interrupt_request", None)
        resolve_session = getattr(client, "resolve_interrupt_session", None)
        discard_request = getattr(client, "discard_interrupt_request", None)

        self._remember_request = remember_request if callable(remember_request) else None
        self._resolve_request = resolve_request if callable(resolve_request) else None
        self._resolve_session = resolve_session if callable(resolve_session) else None
        self._discard_request = discard_request if callable(discard_request) else None

    async def remember_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None = None,
        credential_id: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        details: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        if self._remember_request is None:
            return
        request_kwargs: dict[str, Any] = {
            "request_id": request_id,
            "session_id": session_id,
            "interrupt_type": interrupt_type,
        }
        if identity is not None:
            request_kwargs["identity"] = identity
        if credential_id is not None:
            request_kwargs["credential_id"] = credential_id
        if task_id is not None:
            request_kwargs["task_id"] = task_id
        if context_id is not None:
            request_kwargs["context_id"] = context_id
        if details is not None:
            request_kwargs["details"] = details
        if ttl_seconds is not None:
            request_kwargs["ttl_seconds"] = ttl_seconds
        await self._remember_request(**request_kwargs)

    async def resolve_request(self, request_id: str) -> InterruptRequestResolution:
        if self._resolve_request is not None:
            status, binding = await self._resolve_request(request_id)
            return InterruptRequestResolution(status=status, binding=binding)
        if self._resolve_session is not None:
            # Keep session-only clients working while newer call sites prefer bindings.
            session_id = await self._resolve_session(request_id)
            if isinstance(session_id, str) and session_id:
                return InterruptRequestResolution(status="active", binding=None)
        return InterruptRequestResolution(status="missing", binding=None)

    async def discard_request(self, request_id: str) -> None:
        if self._discard_request is None:
            return
        await self._discard_request(request_id)

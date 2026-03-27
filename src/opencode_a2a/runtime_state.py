from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InterruptRequestBinding:
    request_id: str
    session_id: str
    interrupt_type: str
    identity: str | None
    task_id: str | None
    context_id: str | None
    details: dict[str, Any] | None
    expires_at: float


@dataclass(frozen=True)
class InterruptRequestTombstone:
    request_id: str
    expires_at: float

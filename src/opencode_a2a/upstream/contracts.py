"""Protocol-neutral data exchanged with an upstream execution adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ExecutionState(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AUTH_REQUIRED = "auth_required"


class UpdateKind(StrEnum):
    OTHER = "other"
    PART_UPDATED = "part_updated"
    PART_DELTA = "part_delta"


@dataclass(frozen=True)
class SessionRef:
    id: str


@dataclass(frozen=True)
class PromptReceipt:
    session: SessionRef
    message_id: str | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    session: SessionRef
    state: ExecutionState
    output: str
    message_id: str | None = None
    usage: Mapping[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_state: ExecutionState | None = None
    upstream_status: int | None = None
    response: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PromptSubmission:
    receipt: PromptReceipt
    outcome: ExecutionOutcome | None


@dataclass(frozen=True)
class Intervention:
    request_id: str
    type: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class ExecutionUpdate:
    kind: UpdateKind
    session_id: str | None = None
    message_id: str | None = None
    part_id: str | None = None
    part_type: str | None = None
    role: str | None = None
    delta_field: str | None = None
    delta: str | None = None
    snapshot: str | None = None
    tool_payload: Mapping[str, Any] | None = None
    progress: Mapping[str, Any] | None = None
    progress_identity: str | None = None
    usage: Mapping[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_state: ExecutionState | None = None
    upstream_status: int | None = None
    terminal: ExecutionOutcome | None = None
    intervention: Intervention | None = None
    resolved_request_id: str | None = None
    resolved_intervention_type: str | None = None
    resolution: str | None = None


class UpstreamAdapter(Protocol):
    """Small execution surface shared by upstream protocol implementations."""

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> str: ...

    async def submit_prompt(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: Sequence[Mapping[str, Any]] | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
        model_override: Mapping[str, Any] | None = None,
        timeout_override: float | None | object = ...,
    ) -> PromptSubmission: ...

    def stream_updates(
        self,
        stop_event: asyncio.Event | None = None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> AsyncIterator[ExecutionUpdate]: ...

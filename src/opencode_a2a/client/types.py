"""Public type hints for the lightweight opencode-a2a client facade."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)

A2AClientEvent = (
    Message | tuple[Task, TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None] | None
)
A2AClientEventStream = AsyncIterator[A2AClientEvent]
A2AClientMetadata = Mapping[str, Any]

__all__ = ["A2AClientEvent", "A2AClientEventStream", "A2AClientMetadata"]

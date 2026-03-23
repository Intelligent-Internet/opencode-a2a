from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a.server.events.event_queue import EventQueue
from a2a.types import Artifact, Part, TaskArtifactUpdateEvent


async def _enqueue_artifact_update(
    *,
    event_queue: EventQueue,
    task_id: str,
    context_id: str,
    artifact_id: str,
    part: Part,
    append: bool | None,
    last_chunk: bool | None,
    artifact_metadata: Mapping[str, Any] | None = None,
    event_metadata: Mapping[str, Any] | None = None,
) -> None:
    normalized_last_chunk = True if last_chunk is True else None
    artifact = Artifact(
        artifact_id=artifact_id,
        parts=[part],
        metadata=dict(artifact_metadata) if artifact_metadata else None,
    )
    await event_queue.enqueue_event(
        TaskArtifactUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            artifact=artifact,
            append=append,
            last_chunk=normalized_last_chunk,
            metadata=dict(event_metadata) if event_metadata else None,
        )
    )

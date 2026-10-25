from __future__ import annotations

from a2a.server.cluster import TaskVersion, VersionedTaskStore
from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types import Task

from opencode_a2a.server.context_helpers import normalize_server_call_context
from opencode_a2a.server.task_store import as_versioned_task_store


async def save_task(
    task_store: TaskStore | VersionedTaskStore,
    task: Task,
    context: ServerCallContext | None = None,
) -> TaskVersion:
    normalized_context = normalize_server_call_context(context)
    versioned_store = as_versioned_task_store(task_store)
    initialize = getattr(versioned_store, "initialize", None)
    if callable(initialize):
        await initialize()
    stored = await versioned_store.get(task.id, normalized_context)
    return await versioned_store.save(
        task,
        event=None,
        prev=stored.task if stored is not None else None,
        prev_version=stored.version if stored is not None else TaskVersion.MISSING,
        context=normalized_context,
    )

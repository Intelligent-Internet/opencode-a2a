from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.types import Message


def _build_history(context: RequestContext) -> list[Message]:
    if context.current_task and context.current_task.history:
        history = list(context.current_task.history)
    else:
        history = []
        if context.message:
            history.append(context.message)
    return history


def _iter_metadata_maps(context: RequestContext, namespace: str):
    try:
        meta = context.metadata
    except Exception:
        meta = None

    if isinstance(meta, Mapping):
        namespaced_meta = meta.get(namespace)
        if isinstance(namespaced_meta, Mapping):
            yield namespaced_meta

    if context.message is not None:
        msg_meta = getattr(context.message, "metadata", None) or {}
        if isinstance(msg_meta, Mapping):
            namespaced_meta = msg_meta.get(namespace)
            if isinstance(namespaced_meta, Mapping):
                yield namespaced_meta


def _extract_namespaced_string_metadata(
    context: RequestContext,
    *,
    namespace: str,
    path: tuple[str, ...],
) -> str | None:
    for namespaced_meta in _iter_metadata_maps(context, namespace):
        current: Any = namespaced_meta
        for part in path[:-1]:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(part)
        if not isinstance(current, Mapping):
            continue
        candidate = current.get(path[-1])
        if isinstance(candidate, str):
            value = candidate.strip()
            if value:
                return value
    return None


def _extract_shared_session_id(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace="shared",
        path=("session", "id"),
    )


def _extract_shared_model(context: RequestContext) -> dict[str, str] | None:
    provider_id = _extract_namespaced_string_metadata(
        context,
        namespace="shared",
        path=("model", "providerID"),
    )
    model_id = _extract_namespaced_string_metadata(
        context,
        namespace="shared",
        path=("model", "modelID"),
    )
    if provider_id is None or model_id is None:
        return None
    return {"providerID": provider_id, "modelID": model_id}


def _extract_opencode_directory(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace="opencode",
        path=("directory",),
    )


def _extract_opencode_workspace_id(context: RequestContext) -> str | None:
    return _extract_namespaced_string_metadata(
        context,
        namespace="opencode",
        path=("workspace", "id"),
    )

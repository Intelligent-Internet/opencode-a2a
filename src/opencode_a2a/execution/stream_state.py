from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BlockType(StrEnum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"


@dataclass(frozen=True)
class _NormalizedStreamChunk:
    part: Any
    content_key: str
    accumulate_content: bool
    append: bool
    block_type: BlockType
    internal_source: str
    shared_source: str
    message_id: str | None
    role: str | None


@dataclass(frozen=True)
class _PendingDelta:
    field: str
    delta: str
    message_id: str | None


@dataclass
class _StreamPartState:
    block_type: BlockType
    message_id: str | None
    role: str | None
    buffer: str = ""
    saw_delta: bool = False


def _merge_token_usage(
    base: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if base is None and incoming is None:
        return None
    merged: dict[str, Any] = dict(base) if base else {}
    if incoming:
        for key, value in incoming.items():
            if value is None:
                continue
            if key == "raw" and isinstance(value, Mapping):
                existing = merged.get("raw")
                if isinstance(existing, Mapping):
                    merged["raw"] = {**dict(existing), **dict(value)}
                else:
                    merged["raw"] = dict(value)
                continue
            merged[key] = value
    return merged or None


@dataclass
class _StreamOutputState:
    user_text: str
    stable_message_id: str
    event_id_namespace: str
    content_buffers: dict[BlockType, str] = field(default_factory=dict)
    progress_buffers: dict[str, str] = field(default_factory=dict)
    token_usage: dict[str, Any] | None = None
    upstream_error: Any | None = None
    pending_interrupt_request_ids: set[str] = field(default_factory=set)
    saw_any_chunk: bool = False
    emitted_stream_chunk: bool = False
    sequence: int = 0

    def should_drop_initial_user_echo(
        self,
        text: str,
        *,
        block_type: BlockType,
        role: str | None,
    ) -> bool:
        if role is not None:
            return False
        if block_type != BlockType.TEXT:
            return False
        if self.saw_any_chunk:
            return False
        user_text = self.user_text.strip()
        return bool(user_text) and text.strip() == user_text

    def register_chunk(
        self,
        *,
        block_type: BlockType,
        content_key: str,
        append: bool,
        accumulate_content: bool = True,
    ) -> tuple[bool, bool]:
        previous = self.content_buffers.get(block_type, "")
        next_value = f"{previous}{content_key}" if append and accumulate_content else content_key
        if next_value == previous:
            return False, False
        self.content_buffers[block_type] = next_value
        self.saw_any_chunk = True
        effective_append = self.emitted_stream_chunk
        self.emitted_stream_chunk = True
        return True, effective_append

    def register_progress(self, *, identity: str, content_key: str) -> bool:
        previous = self.progress_buffers.get(identity)
        if previous == content_key:
            return False
        self.progress_buffers[identity] = content_key
        return True

    def should_emit_final_snapshot(self, text: str) -> bool:
        if not text.strip():
            return False
        existing = self.content_buffers.get(BlockType.TEXT, "")
        if existing.strip() == text.strip():
            return False
        self.content_buffers[BlockType.TEXT] = text
        self.saw_any_chunk = True
        return True

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def resolve_message_id(self, message_id: str | None) -> str:
        if isinstance(message_id, str):
            normalized = message_id.strip()
            if normalized:
                return normalized
        return self.stable_message_id

    def build_event_id(self, sequence: int) -> str:
        return f"{self.event_id_namespace}:{sequence}"

    def ingest_token_usage(self, usage: Mapping[str, Any] | None) -> None:
        self.token_usage = _merge_token_usage(self.token_usage, usage)

    def mark_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            return False
        if normalized in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.add(normalized)
        return True

    def clear_interrupt_pending(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            return False
        if normalized not in self.pending_interrupt_request_ids:
            return False
        self.pending_interrupt_request_ids.discard(normalized)
        return True


class _TTLCache:
    """Bounded TTL cache for hashable key -> string value."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        maxsize: int,
        now: Callable[[], float] = time.monotonic,
        refresh_on_get: bool = False,
    ) -> None:
        self._ttl_seconds = int(ttl_seconds)
        self._maxsize = int(maxsize)
        self._now = now
        self._refresh_on_get = bool(refresh_on_get)
        self._store: dict[object, tuple[str, float]] = {}

    def get(self, key: object) -> str | None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return None
        item = self._store.get(key)
        if not item:
            return None
        value, expires_at = item
        now = self._now()
        if expires_at <= now:
            self._store.pop(key, None)
            return None
        if self._refresh_on_get:
            self._store[key] = (value, now + float(self._ttl_seconds))
        return value

    def set(self, key: object, value: str) -> None:
        if self._ttl_seconds <= 0 or self._maxsize <= 0:
            return
        now = self._now()
        expires_at = now + float(self._ttl_seconds)
        self._store[key] = (value, expires_at)
        self._evict_if_needed(now=now)

    def pop(self, key: object) -> None:
        self._store.pop(key, None)

    def _evict_if_needed(self, *, now: float) -> None:
        if len(self._store) <= self._maxsize:
            return
        expired = [key for key, (_, exp) in self._store.items() if exp <= now]
        for key in expired:
            self._store.pop(key, None)
        if len(self._store) <= self._maxsize:
            return
        overflow = len(self._store) - self._maxsize
        by_expiry = sorted(self._store.items(), key=lambda item: item[1][1])
        for key, _ in by_expiry[:overflow]:
            self._store.pop(key, None)


def _build_stream_artifact_metadata(
    *,
    block_type: BlockType,
    shared_source: str,
    message_id: str | None = None,
    role: str | None = None,
    event_id: str | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    stream_meta: dict[str, Any] = {
        "block_type": block_type.value,
        "source": shared_source,
    }
    if message_id:
        stream_meta["message_id"] = message_id
    if role:
        stream_meta["role"] = role
    if event_id:
        stream_meta["event_id"] = event_id
    if sequence is not None:
        stream_meta["sequence"] = sequence
    return {"shared": {"stream": stream_meta}}


def _build_output_metadata(
    *,
    session_id: str | None = None,
    session_title: str | None = None,
    usage: Mapping[str, Any] | None = None,
    stream: Mapping[str, Any] | None = None,
    progress: Mapping[str, Any] | None = None,
    interrupt: Mapping[str, Any] | None = None,
    opencode_private: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    shared_meta: dict[str, Any] = {}

    if session_id:
        session_meta: dict[str, Any] = {"id": session_id}
        if session_title is not None:
            session_meta["title"] = session_title
        shared_meta["session"] = session_meta
    if usage is not None:
        shared_meta["usage"] = dict(usage)
    if stream is not None:
        shared_meta["stream"] = dict(stream)
    if progress is not None:
        shared_meta["progress"] = dict(progress)
    if interrupt is not None:
        shared_meta["interrupt"] = dict(interrupt)
    if shared_meta:
        metadata["shared"] = shared_meta
    if opencode_private:
        metadata["opencode"] = dict(opencode_private)
    return metadata or None

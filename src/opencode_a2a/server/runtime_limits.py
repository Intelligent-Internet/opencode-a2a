"""Inbound admission controls for the A2A runtime.

The server exposes authenticated JSON-RPC and HTTP+JSON surfaces plus a
public Agent Card. Without admission controls a single caller can hold every
concurrency slot or force unbounded SSE output, starving other callers and
accumulating memory, file descriptors, and bandwidth.

This module provides:

- ``SlidingWindowRateLimiter``: process-local sliding-window admission counter
  keyed by credential/principal for authenticated requests and by peer IP for
  the public surface.
- ``apply_stream_budget``: async-generator wrapper that bounds a streaming
  response (total serialized bytes, total duration, and idle gap) and raises
  ``StreamBudgetExceeded`` when a budget is exceeded. The transport layers
  convert that into a well-formed SSE ``error`` event and end the stream
  through the same clean teardown path as a naturally completed stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from starlette.responses import JSONResponse

from ..execution.metrics import emit_metric

logger = logging.getLogger(__name__)

RATE_LIMIT_ERROR_MESSAGE = "Too many requests"
STREAM_BUDGET_ERROR_MESSAGE = "Stream budget exceeded"
_DEFAULT_MAX_RATE_LIMIT_KEYS = 100_000
# Approximate SSE framing overhead per event: ``data: `` prefix, line
# separators, and the compact-JSON slack.
_SSE_EVENT_FRAMING_OVERHEAD = 32


def build_rate_limit_response(retry_after_seconds: float) -> JSONResponse:
    """Build a 429 response carrying a client-safe Retry-After hint."""
    retry_after = max(1, math.ceil(retry_after_seconds))
    return JSONResponse(
        {"error": RATE_LIMIT_ERROR_MESSAGE},
        status_code=429,
        headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
    )


class SlidingWindowRateLimiter:
    """Process-local sliding-window rate limiter.

    Every key tracks the timestamps of admitted requests inside the window.
    Buckets are pruned lazily on access and the key table is capped so memory
    stays bounded even under a flood of distinct keys. All mutations are
    serialized by an asyncio lock so concurrent request handlers observe a
    consistent counter.
    """

    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        max_keys: int = _DEFAULT_MAX_RATE_LIMIT_KEYS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be greater than 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than 0")
        if max_keys <= 0:
            raise ValueError("max_keys must be greater than 0")
        self._max_requests = max_requests
        self._window_seconds = float(window_seconds)
        self._max_keys = max_keys
        self._clock = clock or time.monotonic
        self._entries: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check_and_record(self, key: str) -> bool:
        """Return True when the request is admitted and record it."""
        async with self._lock:
            now = self._clock()
            entries = self._entries.setdefault(key, deque())
            self._prune(entries, now)
            if len(entries) >= self._max_requests:
                return False
            entries.append(now)
            self._evict_if_needed()
            return True

    async def retry_after(self, key: str) -> float:
        """Return the seconds until the oldest recorded request expires."""
        async with self._lock:
            entries = self._entries.get(key)
            if not entries:
                return self._window_seconds
            now = self._clock()
            self._prune(entries, now)
            if not entries:
                return self._window_seconds
            return max(0.0, entries[0] + self._window_seconds - now)

    def _prune(self, entries: deque[float], now: float) -> None:
        while entries and now - entries[0] >= self._window_seconds:
            entries.popleft()

    def _evict_if_needed(self) -> None:
        if len(self._entries) <= self._max_keys:
            return
        # dict preserves insertion order; evict the oldest-inserted key.
        stale_key = next(iter(self._entries))
        del self._entries[stale_key]


class StreamBudgetExceeded(Exception):
    """Raised when a streaming response exceeds its configured budget."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{STREAM_BUDGET_ERROR_MESSAGE}: {reason}")
        self.reason = reason


def json_event_size(item: Any) -> int:
    """Approximate the on-wire SSE bytes for one serialized event item."""
    serialized = json.dumps(
        item,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return len(serialized) + _SSE_EVENT_FRAMING_OVERHEAD


async def apply_stream_budget(
    stream: AsyncIterator[Any],
    *,
    max_bytes: int,
    max_duration_seconds: float,
    idle_timeout_seconds: float,
    clock: Callable[[], float] | None = None,
    size_of: Callable[[Any], int] = json_event_size,
) -> AsyncGenerator[Any, None]:
    """Yield events while enforcing byte, duration, and idle budgets.

    A value of ``0`` disables the corresponding budget. When a budget is
    exceeded ``StreamBudgetExceeded`` is raised and the underlying stream is
    closed first, so the application runs the same cleanup/drain path as a
    client disconnect and the transport emits a well-formed SSE ``error``
    event before ending the response normally.
    """
    resolve_clock = clock or time.monotonic
    started_at: float | None = None
    total_bytes = 0

    try:
        while True:
            try:
                if idle_timeout_seconds > 0:
                    event = await asyncio.wait_for(
                        anext(stream),
                        timeout=idle_timeout_seconds,
                    )
                else:
                    event = await anext(stream)
            except StopAsyncIteration:
                return
            except TimeoutError:
                _reject_budget("idle timeout")
                return

            now = resolve_clock()
            if started_at is None:
                started_at = now
            elif max_duration_seconds > 0 and now - started_at >= max_duration_seconds:
                _reject_budget("duration budget")
                return

            total_bytes += size_of(event)
            if max_bytes > 0 and total_bytes > max_bytes:
                _reject_budget("byte budget")
                return

            yield event
    finally:
        # Close the inner generator so its handler observes GeneratorExit and
        # runs the normal disconnect/drain cleanup.
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def _reject_budget(reason: str) -> None:
    emit_metric("a2a_stream_budget_rejected_total")
    raise StreamBudgetExceeded(reason)

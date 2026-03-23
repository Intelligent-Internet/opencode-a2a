from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass

import httpx
from a2a.types import TaskState

from ..upstream_taxonomy import (
    extract_upstream_error_detail as _extract_upstream_error_detail,
)
from ..upstream_taxonomy import (
    resolve_upstream_http_error_profile as _resolve_upstream_error_profile,
)
from ..opencode_upstream_client import UpstreamContractError


@dataclass(frozen=True)
class _StreamTerminalSignal:
    state: TaskState
    error_type: str | None = None
    message: str | None = None
    upstream_status: int | None = None


@dataclass(frozen=True)
class _UpstreamInBandError:
    error_type: str
    state: TaskState
    message: str
    upstream_status: int | None = None


def _format_upstream_error(
    exc: httpx.HTTPStatusError, *, request: str
) -> tuple[str, TaskState, str]:
    status = exc.response.status_code
    profile = _resolve_upstream_error_profile(status)
    detail = _extract_upstream_error_detail(exc.response)
    if detail:
        return (
            profile.error_type,
            profile.state,
            f"{profile.default_message} ({request}, status={status}, detail={detail}).",
        )
    return (
        profile.error_type,
        profile.state,
        f"{profile.default_message} ({request}, status={status}).",
    )


def _format_stream_terminal_error(
    *,
    detail: str | None,
    status: int | None,
    error_name: str | None,
) -> _StreamTerminalSignal:
    if status is not None:
        profile = _resolve_upstream_error_profile(status)
        if detail:
            message = (
                f"{profile.default_message} (session.error, status={status}, detail={detail})."
            )
        else:
            message = f"{profile.default_message} (session.error, status={status})."
        return _StreamTerminalSignal(
            state=profile.state,
            error_type=profile.error_type,
            message=message,
            upstream_status=status,
        )

    if error_name == "ProviderAuthError":
        if detail:
            message = (
                "OpenCode rejected the request due to authentication failure "
                f"(session.error, detail={detail})."
            )
        else:
            message = "OpenCode rejected the request due to authentication failure (session.error)."
        return _StreamTerminalSignal(
            state=TaskState.auth_required,
            error_type="UPSTREAM_UNAUTHORIZED",
            message=message,
        )

    if detail:
        message = f"OpenCode execution failed (session.error, detail={detail})."
    elif error_name:
        message = f"OpenCode execution failed (session.error, error={error_name})."
    else:
        message = "OpenCode execution failed (session.error)."
    return _StreamTerminalSignal(
        state=TaskState.failed,
        error_type="UPSTREAM_EXECUTION_ERROR",
        message=message,
    )


def _format_inband_upstream_error(
    *,
    source: str,
    detail: str | None,
    status: int | None,
    error_name: str | None,
) -> _UpstreamInBandError:
    if status is not None:
        profile = _resolve_upstream_error_profile(status)
        if detail:
            message = f"{profile.default_message} ({source}, status={status}, detail={detail})."
        else:
            message = f"{profile.default_message} ({source}, status={status})."
        return _UpstreamInBandError(
            error_type=profile.error_type,
            state=profile.state,
            message=message,
            upstream_status=status,
        )

    if error_name == "ProviderAuthError":
        if detail:
            message = (
                "OpenCode rejected the request due to authentication failure "
                f"({source}, detail={detail})."
            )
        else:
            message = f"OpenCode rejected the request due to authentication failure ({source})."
        return _UpstreamInBandError(
            error_type="UPSTREAM_UNAUTHORIZED",
            state=TaskState.auth_required,
            message=message,
        )

    if detail:
        message = f"OpenCode execution failed ({source}, detail={detail})."
    elif error_name:
        message = f"OpenCode execution failed ({source}, error={error_name})."
    else:
        message = f"OpenCode execution failed ({source})."
    return _UpstreamInBandError(
        error_type="UPSTREAM_EXECUTION_ERROR",
        state=TaskState.failed,
        message=message,
    )


async def _await_stream_terminal_signal(
    *,
    stream_task: asyncio.Task[None] | None,
    terminal_signal: asyncio.Future[_StreamTerminalSignal],
    session_id: str,
) -> _StreamTerminalSignal:
    if terminal_signal.done():
        return terminal_signal.result()
    if stream_task is None:
        raise RuntimeError("Streaming task was not initialized")

    terminal_wait_task = asyncio.create_task(_wait_for_terminal_signal(terminal_signal))
    try:
        done, _pending = await asyncio.wait(
            {stream_task, terminal_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if terminal_wait_task in done:
            return terminal_wait_task.result()
        if stream_task in done:
            with suppress(asyncio.CancelledError):
                await stream_task
            if terminal_signal.done():
                return terminal_signal.result()
            raise UpstreamContractError(
                "OpenCode event stream ended before terminal signal "
                f"(session_id={session_id}, expected session.idle or session.error)"
            )
        return await terminal_wait_task
    finally:
        if not terminal_wait_task.done():
            terminal_wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await terminal_wait_task


async def _wait_for_terminal_signal(
    terminal_signal: asyncio.Future[_StreamTerminalSignal],
) -> _StreamTerminalSignal:
    return await terminal_signal

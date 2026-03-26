"""Polling fallback policy helpers for the A2A client facade."""

from __future__ import annotations

from dataclasses import dataclass

from a2a.types import TaskState

_TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.completed,
        TaskState.canceled,
        TaskState.failed,
        TaskState.rejected,
    }
)
_AUTO_POLLING_TASK_STATES = frozenset(
    {
        TaskState.submitted,
        TaskState.working,
        TaskState.unknown,
    }
)


@dataclass(frozen=True)
class PollingFallbackPolicy:
    """Encapsulates polling fallback configuration and task-state rules."""

    enabled: bool = False
    initial_interval_seconds: float = 0.5
    max_interval_seconds: float = 2.0
    backoff_multiplier: float = 2.0
    timeout_seconds: float = 10.0

    def should_poll_state(self, state: TaskState) -> bool:
        return state in _AUTO_POLLING_TASK_STATES

    def is_terminal_state(self, state: TaskState) -> bool:
        return state in _TERMINAL_TASK_STATES

    def next_interval_seconds(self, current_interval_seconds: float) -> float:
        return min(
            max(current_interval_seconds, 0.0) * self.backoff_multiplier,
            self.max_interval_seconds,
        )


def validate_polling_fallback_policy(policy: PollingFallbackPolicy) -> None:
    """Validate polling fallback settings before they are used at runtime."""
    if policy.initial_interval_seconds <= 0:
        raise ValueError("A2A_CLIENT_POLLING_FALLBACK_INITIAL_INTERVAL_SECONDS must be positive")
    if policy.max_interval_seconds <= 0:
        raise ValueError("A2A_CLIENT_POLLING_FALLBACK_MAX_INTERVAL_SECONDS must be positive")
    if policy.backoff_multiplier < 1.0:
        raise ValueError(
            "A2A_CLIENT_POLLING_FALLBACK_BACKOFF_MULTIPLIER must be greater than or equal to 1"
        )
    if policy.timeout_seconds <= 0:
        raise ValueError("A2A_CLIENT_POLLING_FALLBACK_TIMEOUT_SECONDS must be positive")
    if policy.max_interval_seconds < policy.initial_interval_seconds:
        raise ValueError(
            "A2A_CLIENT_POLLING_FALLBACK_MAX_INTERVAL_SECONDS must be greater than or "
            "equal to A2A_CLIENT_POLLING_FALLBACK_INITIAL_INTERVAL_SECONDS"
        )


__all__ = ["PollingFallbackPolicy", "validate_polling_fallback_policy"]

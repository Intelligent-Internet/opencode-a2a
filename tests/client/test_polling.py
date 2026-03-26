from __future__ import annotations

import pytest
from a2a.types import TaskState

from opencode_a2a.client.polling import (
    PollingFallbackPolicy,
    validate_polling_fallback_policy,
)


def test_polling_policy_state_rules_and_backoff() -> None:
    policy = PollingFallbackPolicy(
        enabled=True,
        initial_interval_seconds=0.5,
        max_interval_seconds=2.0,
        backoff_multiplier=2.0,
        timeout_seconds=10.0,
    )

    assert policy.should_poll_state(TaskState.working) is True
    assert policy.should_poll_state(TaskState.input_required) is False
    assert policy.is_terminal_state(TaskState.completed) is True
    assert policy.is_terminal_state(TaskState.working) is False
    assert policy.next_interval_seconds(0.5) == 1.0
    assert policy.next_interval_seconds(2.0) == 2.0


def test_validate_polling_policy_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="TIMEOUT_SECONDS must be positive"):
        validate_polling_fallback_policy(
            PollingFallbackPolicy(timeout_seconds=0.0),
        )

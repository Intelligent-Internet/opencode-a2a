from __future__ import annotations

import pytest

from opencode_a2a.client.agent_card import (
    normalize_agent_card_endpoint,
)
from opencode_a2a.client.error_mapping import map_agent_card_error
from opencode_a2a.client.errors import A2AAuthenticationError
from tests.support.fake_client_errors import FakeA2AClientHTTPError


@pytest.mark.parametrize(
    ("url", "expected_path"),
    [
        (
            "https://ops.example.com/tenant/.well-known/agent-card.json",
            "/.well-known/agent-card.json",
        ),
        ("https://ops.example.com/tenant/extendedAgentCard", "/extendedAgentCard"),
    ],
)
def test_normalize_agent_card_endpoint_strips_extended_card_paths(
    url: str,
    expected_path: str,
) -> None:
    base_url, agent_card_path = normalize_agent_card_endpoint(url)

    assert base_url == "https://ops.example.com/tenant"
    assert agent_card_path == expected_path


def test_normalize_agent_card_endpoint_requires_absolute_url() -> None:
    with pytest.raises(ValueError, match="absolute URL"):
        normalize_agent_card_endpoint("/relative/path")


def test_map_agent_card_error_http_variant() -> None:
    mapped = map_agent_card_error(FakeA2AClientHTTPError(401, "unauthorized"))

    assert isinstance(mapped, A2AAuthenticationError)
    assert mapped.http_status == 401

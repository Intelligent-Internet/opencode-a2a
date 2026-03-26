from __future__ import annotations

from base64 import b64encode
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.client.errors import A2AClientHTTPError

from opencode_a2a.client.agent_card import (
    build_agent_card_resolver,
    build_resolver_http_kwargs,
    normalize_agent_card_endpoint,
)
from opencode_a2a.client.error_mapping import map_agent_card_error
from opencode_a2a.client.errors import A2AAuthenticationError


@pytest.mark.asyncio
async def test_build_agent_card_resolver_strips_explicit_well_known_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class _FakeResolver:
        def __init__(
            self,
            *,
            base_url: str,
            agent_card_path: str,
            httpx_client: object,
        ) -> None:
            del httpx_client
            captured["base_url"] = base_url
            captured["agent_card_path"] = agent_card_path

        async def get_agent_card(self, **kwargs: object) -> str:
            del kwargs
            return "agent-card"

    monkeypatch.setattr("opencode_a2a.client.agent_card.A2ACardResolver", _FakeResolver)

    resolver = build_agent_card_resolver(
        "https://ops.example.com/tenant/.well-known/agent-card.json",
        AsyncMock(spec=httpx.AsyncClient),
    )
    await resolver.get_agent_card()

    assert captured["base_url"] == "https://ops.example.com/tenant"
    assert captured["agent_card_path"] == "/.well-known/agent-card.json"


def test_normalize_agent_card_endpoint_requires_absolute_url() -> None:
    with pytest.raises(ValueError, match="absolute URL"):
        normalize_agent_card_endpoint("/relative/path")


def test_build_resolver_http_kwargs_uses_bearer_token() -> None:
    assert build_resolver_http_kwargs(bearer_token="peer-token", timeout=7) == {
        "timeout": 7,
        "headers": {"Authorization": "Bearer peer-token"},
    }


def test_build_resolver_http_kwargs_uses_basic_auth() -> None:
    encoded = b64encode(b"user:pass").decode()

    assert build_resolver_http_kwargs(
        bearer_token=None,
        basic_auth="user:pass",
        timeout=7,
    ) == {
        "timeout": 7,
        "headers": {"Authorization": f"Basic {encoded}"},
    }


def test_map_agent_card_error_http_variant() -> None:
    mapped = map_agent_card_error(A2AClientHTTPError(401, "unauthorized"))

    assert isinstance(mapped, A2AAuthenticationError)
    assert mapped.http_status == 401

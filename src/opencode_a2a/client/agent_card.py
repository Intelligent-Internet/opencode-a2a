"""Helpers for agent-card URL normalization and resolver bootstrap."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from a2a.client.card_resolver import A2ACardResolver
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)

from .request_context import build_default_headers


def normalize_agent_card_endpoint(agent_url: str) -> tuple[str, str]:
    parsed_url = urlsplit(agent_url)
    if not parsed_url.scheme or not parsed_url.netloc:
        raise ValueError(f"agent_url must be absolute URL: {agent_url}")

    path = parsed_url.path or ""
    normalized_no_leading = path.rstrip("/").lstrip("/")
    candidate_paths = (
        AGENT_CARD_WELL_KNOWN_PATH,
        PREV_AGENT_CARD_WELL_KNOWN_PATH,
        EXTENDED_AGENT_CARD_PATH,
    )

    base_path = normalized_no_leading
    agent_card_path = AGENT_CARD_WELL_KNOWN_PATH
    for candidate_path in candidate_paths:
        card_suffix = candidate_path.lstrip("/")
        if normalized_no_leading.endswith(card_suffix):
            base_path = normalized_no_leading[: -len(card_suffix)].rstrip("/")
            agent_card_path = candidate_path
            break

    base_url = urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            f"/{base_path}" if base_path else "",
            "",
            "",
        )
    ).rstrip("/")
    return base_url, agent_card_path


def build_agent_card_resolver(
    agent_url: str,
    httpx_client: httpx.AsyncClient,
) -> A2ACardResolver:
    base_url, agent_card_path = normalize_agent_card_endpoint(agent_url)
    return A2ACardResolver(
        httpx_client=httpx_client,
        base_url=base_url,
        agent_card_path=agent_card_path,
    )


def build_resolver_http_kwargs(
    *,
    bearer_token: str | None,
    timeout: float,
) -> dict[str, Any]:
    http_kwargs: dict[str, Any] = {"timeout": timeout}
    default_headers = build_default_headers(bearer_token)
    if default_headers:
        http_kwargs["headers"] = default_headers
    return http_kwargs


__all__ = [
    "build_agent_card_resolver",
    "build_resolver_http_kwargs",
    "normalize_agent_card_endpoint",
]

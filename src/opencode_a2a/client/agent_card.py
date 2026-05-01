"""Helpers for agent-card URL normalization."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from ..a2a_protocol import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)


def normalize_agent_card_endpoint(agent_url: str) -> tuple[str, str]:
    parsed_url = urlsplit(agent_url)
    if not parsed_url.scheme or not parsed_url.netloc:
        raise ValueError(f"agent_url must be absolute URL: {agent_url}")

    path = parsed_url.path or ""
    normalized_no_leading = path.rstrip("/").lstrip("/")
    candidate_paths = (
        AGENT_CARD_WELL_KNOWN_PATH,
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

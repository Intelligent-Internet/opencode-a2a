"""Outbound A2A client network policy (SSRF guard and credential binding).

The embedded ``a2a_call`` tool lets the upstream model choose the target URL,
so without an explicit policy the adapter could connect to private/loopback/
link-local addresses (cloud metadata, internal services) and would attach
globally configured outbound credentials to any endpoint.

This module centralizes the checks:

- only ``http``/``https`` schemes are accepted;
- userinfo credentials in the URL are rejected;
- when ``A2A_CLIENT_ALLOWED_HOSTS`` is configured, the host must match the
  allowlist (exact or ``*.example.com`` wildcard);
- unless ``A2A_CLIENT_ALLOW_PRIVATE_HOSTS`` is set, DNS resolution results are
  validated and private/loopback/link-local/reserved addresses are rejected
  (DNS-rebinding defense);
- outbound credentials may only be attached to hosts that match the allowlist.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class A2ANetworkPolicyError(ValueError):
    """Raised when an outbound agent URL violates the network policy."""


@dataclass(frozen=True)
class NetworkPolicyDecision:
    """Outcome of validating an outbound agent URL."""

    host: str
    allowed_host: bool
    credentials_allowed: bool


def matches_allowed_host(host: str, allowed_hosts: Sequence[str]) -> bool:
    """Return whether ``host`` matches an allowlist entry.

    Entries may be exact hostnames or ``*.example.com`` wildcards. A wildcard
    matches any number of subdomain labels but never the bare apex domain.
    """

    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    for raw_entry in allowed_hosts or ():
        entry = (raw_entry or "").strip().lower().rstrip(".")
        if not entry:
            continue
        if entry.startswith("*."):
            suffix = entry[1:]  # ".example.com"
            apex = suffix[1:]  # "example.com"
            if normalized != apex and normalized.endswith(suffix):
                return True
        elif normalized == entry:
            return True
    return False


def _is_private_address(address: _Address) -> bool:
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def resolve_host_addresses(host: str) -> tuple[str, ...]:
    """Resolve ``host`` to a de-duplicated tuple of IP address strings."""

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise A2ANetworkPolicyError(f"Agent URL host could not be resolved: {host!r}") from exc
    addresses: list[str] = []
    for info in infos:
        ip = str(info[4][0])
        if ip not in addresses:
            addresses.append(ip)
    return tuple(addresses)


async def validate_agent_url(
    agent_url: str,
    *,
    allowed_hosts: Sequence[str] | None = None,
    allow_private_hosts: bool = False,
) -> NetworkPolicyDecision:
    """Validate an outbound agent URL against the configured network policy."""

    normalized = (agent_url or "").strip()
    if not normalized:
        raise A2ANetworkPolicyError("agent_url is required")

    parsed = urlsplit(normalized)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise A2ANetworkPolicyError(
            f"Agent URL scheme must be http or https, got {scheme or 'empty'}"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise A2ANetworkPolicyError("Agent URL must include a host")
    if parsed.username or parsed.password:
        raise A2ANetworkPolicyError("Agent URL must not include userinfo credentials")

    allowlist = tuple(
        (item or "").strip().lower().rstrip(".")
        for item in allowed_hosts or ()
        if (item or "").strip()
    )
    matched = matches_allowed_host(host, allowlist)
    if allowlist and not matched:
        raise A2ANetworkPolicyError(
            f"Agent URL host {host!r} is not allowed by A2A_CLIENT_ALLOWED_HOSTS"
        )

    if not allow_private_hosts:
        try:
            addresses = await resolve_host_addresses(host)
        except A2ANetworkPolicyError:
            raise
        except OSError as exc:
            raise A2ANetworkPolicyError(f"Agent URL host could not be resolved: {host!r}") from exc
        private = [
            address for address in addresses if _is_private_address(ipaddress.ip_address(address))
        ]
        if private:
            raise A2ANetworkPolicyError(
                f"Agent URL host {host!r} resolves to a private/loopback address: {private[0]}"
            )

    return NetworkPolicyDecision(
        host=host,
        allowed_host=matched,
        credentials_allowed=bool(allowlist) and matched,
    )

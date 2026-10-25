"""Preconditions for the experimental, dedicated V2 execution profile."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from ..config import Settings
from ..opencode_upstream_client import UpstreamContractError


def validate_exclusive_settings(settings: Settings) -> None:
    address = urlsplit(settings.opencode_base_url)
    if address.hostname != "127.0.0.1" or address.username or address.password:
        raise ValueError("V2 exclusive execution requires a private loopback upstream URL")
    if not settings.opencode_auth_password:
        raise ValueError("V2 exclusive execution requires separate upstream Basic credentials")
    if not settings.opencode_workspace_root:
        raise ValueError("V2 exclusive execution requires an explicit workspace root")
    if settings.a2a_task_store_backend != "database":
        raise ValueError("V2 exclusive execution requires durable database-backed ownership")
    if settings.opencode_agent or settings.opencode_system or settings.opencode_variant:
        raise ValueError(
            "V2 exclusive execution does not support V1 agent/system/variant overrides"
        )


def validate_exclusive_configuration(entries: object, plugins: object) -> None:
    if not isinstance(entries, list) or not isinstance(plugins, Mapping):
        raise UpstreamContractError("Cannot verify V2 exclusive configuration")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise UpstreamContractError("Invalid V2 configuration entry")
        if entry.get("type") != "document":
            continue
        info = entry.get("info")
        if not isinstance(info, Mapping):
            raise UpstreamContractError("Invalid V2 configuration document")
        if info.get("warming") not in (None, False):
            raise UpstreamContractError("V2 exclusive execution prohibits warming")
        mcp = info.get("mcp")
        if info.get("plugins") or (isinstance(mcp, Mapping) and mcp.get("servers")):
            raise UpstreamContractError("V2 exclusive execution prohibits custom plugins and MCP")
    inventory = plugins.get("data")
    if not isinstance(inventory, list):
        raise UpstreamContractError("Invalid V2 plugin inventory")
    for plugin in inventory:
        source = plugin.get("source") if isinstance(plugin, Mapping) else None
        if not isinstance(source, Mapping) or source.get("type") != "builtin":
            raise UpstreamContractError("V2 exclusive execution permits builtin plugins only")


def exclusive_permissions() -> list[dict[str, str]]:
    return [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*", "effect": "ask"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
    ]

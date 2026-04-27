from __future__ import annotations

from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

PREV_AGENT_CARD_WELL_KNOWN_PATH = "/.well-known/agent.json"
EXTENDED_AGENT_CARD_PATH = "/agent/authenticatedExtendedCard"
CORE_JSONRPC_METHODS = tuple(JsonRpcDispatcher.METHOD_TO_MODEL)

__all__ = [
    "AGENT_CARD_WELL_KNOWN_PATH",
    "CORE_JSONRPC_METHODS",
    "EXTENDED_AGENT_CARD_PATH",
    "PREV_AGENT_CARD_WELL_KNOWN_PATH",
]

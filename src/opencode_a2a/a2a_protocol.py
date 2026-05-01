from __future__ import annotations

from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH as SDK_AGENT_CARD_WELL_KNOWN_PATH

AGENT_CARD_WELL_KNOWN_PATH = SDK_AGENT_CARD_WELL_KNOWN_PATH
EXTENDED_AGENT_CARD_PATH = "/extendedAgentCard"
EXTENDED_AGENT_CARD_PATHS = (EXTENDED_AGENT_CARD_PATH,)
CORE_JSONRPC_METHODS = tuple(JsonRpcDispatcher.METHOD_TO_MODEL)

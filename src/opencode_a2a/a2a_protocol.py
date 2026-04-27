from __future__ import annotations

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

PREV_AGENT_CARD_WELL_KNOWN_PATH = "/.well-known/agent.json"
EXTENDED_AGENT_CARD_PATH = "/agent/authenticatedExtendedCard"

V1_JSONRPC_METHOD_TO_LEGACY_METHOD: dict[str, str] = {
    "CancelTask": "tasks/cancel",
    "CreateTaskPushNotificationConfig": "tasks/pushNotificationConfig/set",
    "DeleteTaskPushNotificationConfig": "tasks/pushNotificationConfig/delete",
    "GetExtendedAgentCard": "agent/getAuthenticatedExtendedCard",
    "GetTask": "tasks/get",
    "GetTaskPushNotificationConfig": "tasks/pushNotificationConfig/get",
    "ListTasks": "tasks/list",
    "ListTaskPushNotificationConfigs": "tasks/pushNotificationConfig/list",
    "SendMessage": "message/send",
    "SendStreamingMessage": "message/stream",
    "SubscribeToTask": "tasks/resubscribe",
}

LEGACY_JSONRPC_METHOD_TO_V1_METHOD = {
    legacy: method for method, legacy in V1_JSONRPC_METHOD_TO_LEGACY_METHOD.items()
}

__all__ = [
    "AGENT_CARD_WELL_KNOWN_PATH",
    "EXTENDED_AGENT_CARD_PATH",
    "LEGACY_JSONRPC_METHOD_TO_V1_METHOD",
    "PREV_AGENT_CARD_WELL_KNOWN_PATH",
    "V1_JSONRPC_METHOD_TO_LEGACY_METHOD",
]

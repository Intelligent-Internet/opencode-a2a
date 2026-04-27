from __future__ import annotations

import logging
import uuid
from typing import Any

from a2a.types import StreamResponse, TaskState

from ..client.payload_text import extract_text
from .tool_error_mapping import build_tool_error, map_a2a_tool_exception

logger = logging.getLogger(__name__)


async def maybe_handle_tools(
    raw_response: dict[str, Any],
    *,
    a2a_client_manager,
) -> list[dict[str, Any]] | None:
    parts = raw_response.get("parts", [])
    if not isinstance(parts, list):
        return None

    results: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "tool":
            continue

        state = part.get("state")
        if not isinstance(state, dict) or state.get("status") != "calling":
            continue

        tool_name = part.get("tool")
        if tool_name == "a2a_call":
            result = await handle_a2a_call_tool(part, a2a_client_manager=a2a_client_manager)
            if result:
                results.append(result)

    return results if results else None


async def handle_a2a_call_tool(
    part: dict[str, Any],
    *,
    a2a_client_manager,
) -> dict[str, Any]:
    call_id = part.get("callID") or str(uuid.uuid4())
    tool_name = part.get("tool") or "a2a_call"
    state = part.get("state", {})
    inputs = state.get("input", {})

    if not isinstance(inputs, dict):
        return {
            "call_id": call_id,
            "tool": tool_name,
            **build_tool_error(
                error_code="a2a_invalid_input",
                error="Invalid a2a_call input payload",
            ),
        }

    agent_url = inputs.get("url")
    message = inputs.get("message")
    if not agent_url or not message:
        return {
            "call_id": call_id,
            "tool": tool_name,
            **build_tool_error(
                error_code="a2a_missing_required_input",
                error="Missing required a2a_call url or message",
            ),
        }

    if a2a_client_manager is None:
        return {
            "call_id": call_id,
            "tool": tool_name,
            **build_tool_error(
                error_code="a2a_client_manager_unavailable",
                error="A2A client manager is not available",
            ),
        }

    try:
        event: StreamResponse | None = None
        result_text = ""
        async with a2a_client_manager.borrow_client(agent_url) as client:
            async for current_event in client.send_message(message):
                event = current_event
                extracted = extract_text(current_event)
                if extracted:
                    result_text = merge_streamed_tool_output(result_text, extracted)

        if result_text:
            return {
                "call_id": call_id,
                "tool": tool_name,
                "output": result_text,
            }

        if (
            event is not None
            and event.HasField("task")
            and event.task.HasField("status")
            and event.task.status.state == TaskState.TASK_STATE_COMPLETED
        ):
            return {
                "call_id": call_id,
                "tool": tool_name,
                "output": "Task completed.",
            }

        return {
            "call_id": call_id,
            "tool": tool_name,
            **build_tool_error(
                error_code="a2a_unexpected_response",
                error="Remote A2A peer returned an unexpected response type",
                error_meta={"response_type": type(event).__name__},
            ),
        }
    except Exception as exc:
        logger.exception("A2A tool call failed")
        return {
            "call_id": call_id,
            "tool": tool_name,
            **map_a2a_tool_exception(exc),
        }


def merge_streamed_tool_output(current: str, incoming: str) -> str:
    if not current:
        return incoming
    if incoming == current or incoming in current:
        return current
    if incoming.startswith(current):
        return incoming
    if current.startswith(incoming):
        return current
    separator = (
        ""
        if current.endswith(("\n", " ", "\t")) or incoming.startswith(("\n", " ", "\t"))
        else "\n"
    )
    return f"{current}{separator}{incoming}"

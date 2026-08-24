from __future__ import annotations

import json
from base64 import b64encode

import httpx
import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageResponse,
    Task,
    TaskState,
    TaskStatus,
)
from google.protobuf.json_format import MessageToDict

from opencode_a2a.client import A2AClient
from opencode_a2a.client.config import A2AClientSettings

_PEER_URL = "https://peer.example.com"
_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _agent_card(protocol_binding: str) -> AgentCard:
    return AgentCard(
        name="HTTP stub peer",
        description="Exercises the real SDK HTTP transports.",
        version="1.0",
        supported_interfaces=[
            AgentInterface(
                url=f"{_PEER_URL}/",
                protocol_binding=protocol_binding,
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )


def _http_stub(
    requests: dict[str, httpx.Request],
    protocol_binding: str,
) -> httpx.MockTransport:
    card = _agent_card(protocol_binding)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            requests["GetAgentCard"] = request
            return httpx.Response(200, json=MessageToDict(card))

        payload = json.loads(request.content) if request.content else None
        if protocol_binding == "JSONRPC":
            method = payload["method"]
        elif request.url.path == "/message:send":
            method = "SendMessage"
        elif request.url.path == "/tasks/task-1":
            method = "GetTask"
        else:  # pragma: no cover - keeps unexpected SDK calls visible
            raise AssertionError(f"Unexpected REST request: {request.method} {request.url}")
        requests[method] = request
        if method == "SendMessage":
            result = MessageToDict(
                SendMessageResponse(
                    message=Message(
                        message_id="reply-1",
                        role=Role.ROLE_AGENT,
                        parts=[Part(text="ok")],
                    )
                )
            )
        elif method == "GetTask":
            result = MessageToDict(
                Task(
                    id="task-1",
                    context_id="context-1",
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )
        else:  # pragma: no cover - keeps unexpected SDK calls visible
            raise AssertionError(f"Unexpected JSON-RPC method: {method}")
        if protocol_binding == "JSONRPC":
            result = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        return httpx.Response(200, json=result)

    return httpx.MockTransport(handle)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol_binding",
    ("JSONRPC", "HTTP+JSON"),
    ids=("jsonrpc", "rest"),
)
@pytest.mark.parametrize(
    ("bearer_token", "basic_auth", "expected_authorization"),
    [
        ("peer-token", None, "Bearer peer-token"),
        (None, "user:pass", f"Basic {b64encode(b'user:pass').decode()}"),
    ],
    ids=("bearer", "basic"),
)
async def test_sdk_http_requests_send_configured_headers(
    protocol_binding: str,
    bearer_token: str | None,
    basic_auth: str | None,
    expected_authorization: str,
) -> None:
    requests: dict[str, httpx.Request] = {}
    settings = A2AClientSettings(
        bearer_token=bearer_token,
        basic_auth=basic_auth,
        supported_transports=(protocol_binding,),
    )
    async with httpx.AsyncClient(transport=_http_stub(requests, protocol_binding)) as http_client:
        client = A2AClient(_PEER_URL, settings=settings, httpx_client=http_client)

        await client.send("hello", metadata={"traceparent": _TRACEPARENT})
        await client.get_task("task-1", extensions=["https://example.com/ext"])

    for method in ("GetAgentCard", "SendMessage", "GetTask"):
        assert requests[method].headers["Authorization"] == expected_authorization
        assert requests[method].headers["A2A-Version"] == "1.0"
    assert requests["SendMessage"].headers["traceparent"] == _TRACEPARENT
    assert requests["GetTask"].headers["A2A-Extensions"] == "https://example.com/ext"


@pytest.mark.asyncio
async def test_sdk_jsonrpc_request_preserves_explicit_authorization_override() -> None:
    requests: dict[str, httpx.Request] = {}
    settings = A2AClientSettings(
        bearer_token="default-token",
        supported_transports=("JSONRPC",),
    )
    async with httpx.AsyncClient(transport=_http_stub(requests, "JSONRPC")) as http_client:
        client = A2AClient(_PEER_URL, settings=settings, httpx_client=http_client)

        await client.send(
            "hello",
            metadata={"authorization": "Bearer explicit-token"},
        )

    assert requests["SendMessage"].headers["Authorization"] == "Bearer explicit-token"

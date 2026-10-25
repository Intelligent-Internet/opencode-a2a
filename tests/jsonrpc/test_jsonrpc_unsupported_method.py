import httpx
import pytest

from opencode_a2a.contracts.extensions import SESSION_BINDING_EXTENSION_URI, catalog
from opencode_a2a.protocol_versions import A2A_PROTOCOL_VERSION
from opencode_a2a.server.application import create_app
from opencode_a2a.upstream.v2 import V2UpstreamAdapter
from tests.support.settings import make_settings

_V2_EXTENSION_METHODS = (
    *catalog.SESSION_METHODS.values(),
    *catalog.PROVIDER_DISCOVERY_METHODS.values(),
    *catalog.WORKSPACE_CONTROL_METHODS.values(),
    *catalog.INTERRUPT_RECOVERY_METHODS.values(),
    *catalog.INTERRUPT_CALLBACK_METHODS.values(),
)


@pytest.mark.asyncio
async def test_unsupported_method_returns_unified_error() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 123, "method": "unsupported.method", "params": {}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 123
    assert "error" in body
    error = body["error"]
    assert error["code"] == -32601
    assert error["message"] == "Method not found"

    data = error["data"]
    assert data["method"] == "unsupported.method"
    assert "supportedMethods" in data
    assert "SendMessage" in data["supportedMethods"]
    assert "opencode.sessions.list" in data["supportedMethods"]
    assert data["protocolVersion"] == A2A_PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_v2_rejects_unimplemented_opencode_extension_methods() -> None:
    app = create_app(make_settings(opencode_api_version="v2"))
    assert isinstance(app.state.upstream_client, V2UpstreamAdapter)
    openapi = app.openapi()
    jsonrpc_contract = openapi["paths"]["/"]["post"]
    assert "x-a2a-extension-contracts" not in jsonrpc_contract
    assert "fail before any upstream request" in jsonrpc_contract["description"]
    assert "examples" not in jsonrpc_contract["requestBody"]["content"]["application/json"]
    for path in ("/message:send", "/message:stream"):
        rest_contract = openapi["paths"][path]["post"]
        assert "fails before any upstream request" in rest_contract["description"]
        assert "examples" not in rest_contract["requestBody"]["content"]["application/json"]
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        extended_card = await client.get(
            "/extendedAgentCard",
            headers={"Authorization": "Bearer test-token"},
        )
        rpc_extended_card = await client.post(
            "/",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Extensions": SESSION_BINDING_EXTENSION_URI,
            },
            json={
                "jsonrpc": "2.0",
                "id": 122,
                "method": "GetExtendedAgentCard",
                "params": {},
            },
        )
        response = await client.post(
            "/",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Extensions": SESSION_BINDING_EXTENSION_URI,
            },
            json={
                "jsonrpc": "2.0",
                "id": 123,
                "method": "opencode.sessions.prompt_async",
                "params": {"sessionID": "ses-1", "parts": [{"type": "text", "text": "hello"}]},
            },
        )

    assert extended_card.status_code == 200
    assert not extended_card.json()["capabilities"].get("extensions")
    assert [skill["id"] for skill in extended_card.json()["skills"]] == [
        "a2a-execution-unavailable"
    ]
    assert rpc_extended_card.status_code == 200
    assert "A2A-Extensions" not in rpc_extended_card.headers
    assert not rpc_extended_card.json()["result"]["capabilities"].get("extensions")
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32601
    assert body["error"]["message"] == "Method not found"
    assert "opencode.sessions.prompt_async" not in body["error"]["data"]["supportedMethods"]
    assert "opencode.sessions.list" not in body["error"]["data"]["supportedMethods"]
    assert "A2A-Extensions" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("method", _V2_EXTENSION_METHODS)
async def test_v2_rejects_every_catalogued_extension_before_upstream_http(
    method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(make_settings(opencode_api_version="v2"))
    upstream_calls: list[tuple[str, str]] = []

    async def record_upstream_call(path: str, **_kwargs) -> None:
        upstream_calls.append((method, path))
        raise AssertionError("V2 extension dispatch must not make an upstream HTTP request")

    monkeypatch.setattr(app.state.upstream_client, "_get_json", record_upstream_call)
    monkeypatch.setattr(app.state.upstream_client, "_post_json", record_upstream_call)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Extensions": SESSION_BINDING_EXTENSION_URI,
            },
            json={"jsonrpc": "2.0", "id": 123, "method": method, "params": {}},
        )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601
    assert response.json()["error"]["message"] == "Method not found"
    assert method not in response.json()["error"]["data"]["supportedMethods"]
    assert response.json()["error"]["data"]["supportedMethods"] == list(
        catalog.CORE_JSONRPC_METHODS
    )
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_v2_core_message_paths_fail_before_upstream_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(make_settings(opencode_api_version="v2"))
    upstream_calls: list[tuple[str, str]] = []

    async def record_upstream_call(path: str, **_kwargs) -> None:
        upstream_calls.append(("message", path))
        raise AssertionError("V2 message execution must not make an upstream HTTP request")

    monkeypatch.setattr(app.state.upstream_client, "_get_json", record_upstream_call)
    monkeypatch.setattr(app.state.upstream_client, "_post_json", record_upstream_call)
    transport = httpx.ASGITransport(app=app)
    message = {
        "messageId": "msg-v2-failfast",
        "role": "ROLE_USER",
        "parts": [{"text": "hello"}],
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        send_message = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 124,
                "method": "SendMessage",
                "params": {"message": message},
            },
        )
        send_streaming = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 125,
                "method": "SendStreamingMessage",
                "params": {"message": message},
            },
        )

    assert send_message.status_code == 200
    failed_task = send_message.json()["result"]["task"]
    assert failed_task["status"]["state"] == "TASK_STATE_FAILED"
    assert "No prompt was submitted" in failed_task["status"]["message"]["parts"][0]["text"]
    assert send_streaming.status_code == 200
    assert send_streaming.json()["error"]["message"] == "Streaming is not supported by the agent"
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_unsupported_method_uses_requested_protocol_version() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Version": "1.0",
            },
            json={"jsonrpc": "2.0", "id": 123, "method": "unsupported.method", "params": {}},
        )

    assert response.status_code == 200
    assert response.headers["A2A-Version"] == "1.0"
    body = response.json()
    assert body["error"]["message"] == "Method not found"
    assert body["error"]["data"] == {
        "method": "unsupported.method",
        "supportedMethods": body["error"]["data"]["supportedMethods"],
        "protocolVersion": "1.0",
    }
    assert "SendMessage" in body["error"]["data"]["supportedMethods"]


@pytest.mark.asyncio
async def test_sendmessage_uses_canonical_v1_method_dispatch() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 123, "method": "SendMessage", "params": {}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 123
    assert body["error"]["code"] == -32602
    assert body["error"]["message"] == "Invalid parameters"


@pytest.mark.asyncio
async def test_unsupported_v1_minor_version_returns_v1_error_details() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/?A2A-Version=1.1",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 124, "method": "SendMessage", "params": {}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["error"]["code"] == -32001
    assert body["error"]["data"][0] == {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "VERSION_NOT_SUPPORTED",
        "domain": "a2a-protocol.org",
        "metadata": {
            "requestedVersion": "1.1",
            "supportedProtocolVersions": '["1.0"]',
            "defaultProtocolVersion": "1.0",
        },
    }


@pytest.mark.asyncio
async def test_unsupported_version_returns_version_error() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/?A2A-Version=2.0",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 123, "method": "SendMessage", "params": {}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 123
    assert body["error"]["code"] == -32001
    assert body["error"]["data"][0] == {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "VERSION_NOT_SUPPORTED",
        "domain": "a2a-protocol.org",
        "metadata": {
            "requestedVersion": "2.0",
            "supportedProtocolVersions": '["1.0"]',
            "defaultProtocolVersion": "1.0",
        },
    }


@pytest.mark.asyncio
async def test_unsupported_method_notification_returns_204() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "method": "unsupported.method", "params": {}},
        )

    # Even unsupported methods follow notification semantics: if id is missing, return 204.
    # Note: OpencodeSessionManagementJSONRPCApplication._handle_requests
    # returns 204 for notifications
    # if it catches the method. For unsupported methods, it now also returns 204 if id is None.
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_disabled_shell_reports_current_supported_methods() -> None:
    settings = make_settings(test_bearer_token="test-token", a2a_enable_session_shell=False)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 124,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "pwd"},
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    error = body["error"]
    assert error["code"] == -32601
    assert error["data"]["method"] == "opencode.sessions.shell"
    assert "opencode.sessions.shell" not in error["data"]["supportedMethods"]


@pytest.mark.asyncio
async def test_policy_disabled_shell_reports_current_supported_methods() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_enable_session_shell=True,
        a2a_sandbox_mode="read-only",
        a2a_write_access_scope="workspace_only",
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 125,
                "method": "opencode.sessions.shell",
                "params": {
                    "session_id": "s-1",
                    "request": {"agent": "code-reviewer", "command": "pwd"},
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    error = body["error"]
    assert error["code"] == -32601
    assert error["data"]["method"] == "opencode.sessions.shell"
    assert "opencode.sessions.shell" not in error["data"]["supportedMethods"]


@pytest.mark.asyncio
async def test_disabled_workspace_mutation_reports_current_supported_methods() -> None:
    settings = make_settings(test_bearer_token="test-token")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 126,
                "method": "opencode.workspaces.create",
                "params": {"request": {"type": "git"}},
            },
        )

    assert response.status_code == 200
    body = response.json()
    error = body["error"]
    assert error["code"] == -32601
    assert error["data"]["method"] == "opencode.workspaces.create"
    assert "opencode.workspaces.create" not in error["data"]["supportedMethods"]

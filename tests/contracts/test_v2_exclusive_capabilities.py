import json

import httpx
import pytest

from opencode_a2a.contracts.extensions import catalog
from opencode_a2a.server.application import create_app
from tests.support.settings import make_settings


@pytest.mark.asyncio
async def test_exclusive_discovery_and_unsupported_calls_never_reach_upstream() -> None:
    settings = make_settings(
        opencode_api_version="v2",
        opencode_v2_execution="exclusive",
        opencode_workspace_root="/workspace",
        opencode_auth_password="synthetic-upstream-password",
    )
    app = create_app(settings)
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("Unsupported calls must not contact upstream")

    app.state.upstream_client._client._transport = httpx.MockTransport(upstream)
    headers = {"Authorization": "Bearer test-token"}
    methods = {
        *catalog.SESSION_METHODS.values(),
        *catalog.PROVIDER_DISCOVERY_METHODS.values(),
        *catalog.WORKSPACE_CONTROL_METHODS.values(),
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
        ) as client:
            response = await client.get(
                "/.well-known/agent-card.json",
                headers={
                    "A2A-Extensions": (
                        "urn:opencode-a2a:extension:model-selection:v1,"
                        "urn:opencode-a2a:extension:interactive-interrupt:v1"
                    )
                },
            )
            assert response.status_code == 200
            card = response.json()
            assert card["capabilities"]["streaming"] is True
            assert card["defaultInputModes"] == ["text/plain"]
            uris = {extension["uri"] for extension in card["capabilities"]["extensions"]}
            assert "urn:opencode-a2a:extension:interactive-interrupt:v1" in uris
            assert "urn:opencode-a2a:extension:model-selection:v1" not in uris
            assert "model-selection" not in response.headers.get("A2A-Extensions", "")
            schema = (await client.get("/openapi.json")).json()
            root = schema["paths"]["/"]["post"]
            assert "model_selection" not in root["x-a2a-extension-contracts"]
            assert (
                "message_send_file_input"
                not in root["requestBody"]["content"]["application/json"]["examples"]
            )
            for method in sorted(methods):
                result = await client.post(
                    "/", json={"jsonrpc": "2.0", "id": method, "method": method, "params": {}}
                )
                assert result.status_code == 200
                assert result.json()["error"]["code"] == -32601
            assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"shared": {"model": {"providerID": "openai", "modelID": "other"}}},
        {"shared": {"model": {"modelID": "incomplete"}}},
        {"opencode": {"workspace": {"id": "foreign"}}},
    ],
)
async def test_exclusive_model_and_workspace_overrides_fail_before_upstream(metadata: dict) -> None:
    app = create_app(
        make_settings(
            opencode_api_version="v2",
            opencode_v2_execution="exclusive",
            opencode_workspace_root="/workspace",
            opencode_auth_password="synthetic-upstream-password",
        )
    )
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("Overrides must fail before session creation")

    app.state.upstream_client._client._transport = httpx.MockTransport(upstream)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            response = await client.post(
                "/message:send",
                headers={
                    "A2A-Extensions": ",".join(
                        [
                            "urn:opencode-a2a:extension:session-binding:v1",
                            "urn:opencode-a2a:extension:model-selection:v1",
                            "urn:opencode-a2a:extension:workspace-control:v1",
                        ]
                    )
                },
                json={
                    "message": {
                        "messageId": "unsupported",
                        "role": "ROLE_USER",
                        "parts": [{"text": "must not execute"}],
                    },
                    "metadata": metadata,
                },
            )
            assert response.status_code == 200
            assert "TASK_STATE_FAILED" in json.dumps(response.json())
            assert requests == []

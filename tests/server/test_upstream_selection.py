import os
from pathlib import Path
from unittest import mock

import httpx
import pytest

from opencode_a2a.config import Settings
from opencode_a2a.opencode_upstream_client import OpencodeUpstreamClient
from opencode_a2a.server.application import create_app
from opencode_a2a.upstream.v2 import V2UpstreamAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "version", "execution"),
    [
        ({}, "v2", False),
        ({"OPENCODE_API_VERSION": "v2"}, "v2", False),
        ({"OPENCODE_V2_EXECUTION": "exclusive"}, "v2", True),
        ({"OPENCODE_API_VERSION": "v1"}, "v1", True),
    ],
)
async def test_environment_selects_adapter_and_discovery(
    environment: dict[str, str], version: str, execution: bool, tmp_path: Path
) -> None:
    with mock.patch.dict(os.environ, environment, clear=True):
        settings = Settings(
            _env_file=None,
            a2a_static_auth_credentials=(
                {"scheme": "bearer", "token": "test-token", "principal": "automation"},
            ),
            a2a_task_store_database_url=f"sqlite+aiosqlite:///{tmp_path}/tasks.db",
            opencode_workspace_root=str(tmp_path),
            opencode_auth_password="synthetic-selection-password",  # pragma: allowlist secret
        )

    app = create_app(settings)
    expected = V2UpstreamAdapter if version == "v2" else OpencodeUpstreamClient
    assert settings.opencode_api_version == version
    assert type(app.state.upstream_client) is expected
    assert getattr(app.state.upstream_client, "prompt_execution_supported", True) is execution

    requests: list[httpx.Request] = []

    def unexpected_upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("Discovery and disabled execution must not contact upstream")

    app.state.upstream_client._client._transport = httpx.MockTransport(unexpected_upstream)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test-token"},
        ) as client:
            card = await client.get("/.well-known/agent-card.json")
            assert card.status_code == 200
            assert card.json()["capabilities"]["streaming"] is execution
            if not execution:
                assert not card.json()["capabilities"].get("extensions")
                failed = await client.post(
                    "/message:send",
                    json={
                        "message": {
                            "messageId": "default-v2-disabled",
                            "role": "ROLE_USER",
                            "parts": [{"text": "must not execute"}],
                        }
                    },
                )
                assert failed.status_code == 200
                task = failed.json()["task"]
                assert task["status"]["state"] == "TASK_STATE_FAILED"
                assert "No prompt was submitted" in task["status"]["message"]["parts"][0]["text"]
            assert requests == []


def test_default_exclusive_profile_still_rejects_missing_upstream_credentials() -> None:
    with mock.patch.dict(os.environ, {"OPENCODE_V2_EXECUTION": "exclusive"}, clear=True):
        settings = Settings(
            _env_file=None,
            a2a_static_auth_credentials=(
                {"scheme": "bearer", "token": "test-token", "principal": "automation"},
            ),
        )

    assert settings.opencode_api_version == "v2"
    assert settings.opencode_v2_execution == "exclusive"
    with pytest.raises(ValueError, match="separate upstream Basic credentials"):
        create_app(settings)

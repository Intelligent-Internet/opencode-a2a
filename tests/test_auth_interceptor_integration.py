import os

import httpx
import pytest
from a2a.client.auth.credentials import InMemoryContextCredentialStore
from a2a.client.auth.interceptor import AuthInterceptor
from a2a.client.client_factory import ClientConfig
from a2a.client.errors import A2AClientHTTPError
from a2a.client.middleware import ClientCallContext
from a2a.types import TaskQueryParams, TransportProtocol

from opencode_a2a.a2a_client import connect_with_patched_rest


@pytest.mark.asyncio
async def test_auth_interceptor_integration_returns_404_after_auth():
    """Ensure AuthInterceptor allows authorized requests to reach 404 instead of 401."""
    token = os.environ.get("A2A_JWT_TOKEN")
    if not token:
        pytest.skip("A2A_JWT_TOKEN not set")

    base_url = os.environ.get("A2A_URL")
    if not base_url:
        pytest.skip("A2A_URL not set")

    probe_url = f"{base_url.rstrip('/')}/.well-known/agent-card.json"
    try:
        async with httpx.AsyncClient(timeout=2.0) as probe:
            await probe.get(probe_url)
    except httpx.RequestError:
        pytest.skip("A2A service not reachable")

    store = InMemoryContextCredentialStore()
    session_id = "auth-test"
    await store.set_credentials(session_id, "bearerAuth", token)

    context = ClientCallContext(state={"sessionId": session_id})
    interceptors = [AuthInterceptor(store)]

    config = ClientConfig(
        supported_transports=[TransportProtocol.http_json],
        httpx_client=httpx.AsyncClient(timeout=10.0),
        streaming=False,
    )

    client = await connect_with_patched_rest(
        base_url,
        client_config=config,
        interceptors=interceptors,
    )

    try:
        with pytest.raises(A2AClientHTTPError) as excinfo:
            await client.get_task(TaskQueryParams(id="task-does-not-exist"), context=context)
        message = str(excinfo.value)
        assert "404" in message
        assert "401" not in message
    finally:
        await config.httpx_client.aclose()

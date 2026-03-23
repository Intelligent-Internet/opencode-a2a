from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.client import ClientConfig
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONRPCError
from a2a.types import JSONRPCError, JSONRPCErrorResponse

from opencode_a2a.client import A2AClient
from opencode_a2a.client import client as client_module
from opencode_a2a.client.config import A2AClientSettings
from opencode_a2a.client.errors import A2AUnsupportedOperationError


class _FakeCardResolver:
    def __init__(self, card: object) -> None:
        self._card = card

        self.get_calls = 0

    async def get_agent_card(self, **_kwargs: object) -> object:
        self.get_calls += 1
        return self._card


class _FakeClient:
    def __init__(
        self,
        events: list[object] | None = None,
        *,
        fail: BaseException | None = None,
    ):
        self._events = list(events or [])
        self._fail = fail
        self.send_message_inputs: list[tuple[object, object, object]] = []
        self.task_inputs: list[tuple[object, object]] = []
        self.cancel_inputs: list[tuple[object, object]] = []
        self.resubscribe_inputs: list[tuple[object, object]] = []

    async def send_message(self, message, *args: object, **kwargs: object) -> AsyncIterator[object]:
        self.send_message_inputs.append((message, args, kwargs))
        if self._fail:
            raise self._fail
        for event in self._events:
            yield event

    async def get_task(self, params, *args: object, **kwargs: object) -> object:
        self.task_inputs.append((params, kwargs))
        if self._fail:
            raise self._fail
        return {"task_id": params.id}

    async def cancel_task(self, params, *args: object, **kwargs: object) -> object:
        self.cancel_inputs.append((params, kwargs))
        if self._fail:
            raise self._fail
        return {"task_id": params.id, "status": "canceled"}

    async def resubscribe(self, params, *args: object, **kwargs: object) -> AsyncIterator[object]:
        self.resubscribe_inputs.append((params, kwargs))
        if self._fail:
            raise self._fail
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_get_agent_card_cached_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = _FakeCardResolver("agent-card")

    async def _build_card_resolver(self: A2AClient) -> _FakeCardResolver:
        return resolver

    client = A2AClient("http://agent.example.com")
    monkeypatch.setattr(A2AClient, "_build_card_resolver", _build_card_resolver)
    first = await client.get_agent_card()
    second = await client.get_agent_card()
    assert first == second == "agent-card"
    assert resolver.get_calls == 1


@pytest.mark.asyncio
async def test_build_card_resolver_strips_explicit_well_known_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class _FakeResolver:
        def __init__(
            self,
            *,
            base_url: str,
            agent_card_path: str,
            httpx_client: object,
        ) -> None:
            captured["base_url"] = base_url
            captured["agent_card_path"] = agent_card_path

        async def get_agent_card(self, **kwargs: object) -> str:
            return "agent-card"

    monkeypatch.setattr(client_module, "A2ACardResolver", _FakeResolver)

    client = A2AClient("https://ops.example.com/tenant/.well-known/agent-card.json")
    await client.get_agent_card()

    assert captured["base_url"] == "https://ops.example.com/tenant"
    assert captured["agent_card_path"] == "/.well-known/agent-card.json"


@pytest.mark.asyncio
async def test_build_client_uses_settings_and_transport_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_http_client = AsyncMock(spec=httpx.AsyncClient)
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(
            default_timeout=10,
            use_client_preference=True,
            card_fetch_timeout=3,
            supported_transports=("HTTP+JSON",),
        ),
        httpx_client=fake_http_client,
    )

    fake_sdk_client = _FakeClient()
    factory_calls: dict[str, object] = {}

    class _FakeFactory:
        def __init__(self, config: ClientConfig, consumers: list[object] | None = None):
            factory_calls["config"] = config
            factory_calls["consumers"] = consumers

        def create(self, _card: object) -> _FakeClient:
            return fake_sdk_client

    async def _build_card_resolver(self: A2AClient) -> _FakeCardResolver:
        return _FakeCardResolver("agent-card")

    monkeypatch.setattr(client_module, "ClientFactory", _FakeFactory)
    monkeypatch.setattr(A2AClient, "_build_card_resolver", _build_card_resolver)
    actual = await client._build_client()

    config = factory_calls["config"]
    assert isinstance(config, ClientConfig)
    assert config.streaming is True
    assert config.polling is False
    assert config.use_client_preference is True
    assert config.supported_transports == ["HTTP+JSON"]
    assert actual is fake_sdk_client


@pytest.mark.asyncio
async def test_send_returns_last_event(monkeypatch: pytest.MonkeyPatch) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(events=["a", "b", "last"])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )
    response = await client.send("hello")
    assert response == "last"


@pytest.mark.asyncio
async def test_send_message_adds_bearer_token_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(bearer_token="peer-token"),
    )
    fake_client = _FakeClient(events=["ok"])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    result = [event async for event in client.send_message("hello")]

    assert result == ["ok"]
    _, _, kwargs = fake_client.send_message_inputs[0]
    assert kwargs["request_metadata"]["authorization"] == "Bearer peer-token"


@pytest.mark.asyncio
async def test_send_message_preserves_explicit_authorization_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(bearer_token="peer-token"),
    )
    fake_client = _FakeClient(events=["ok"])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    result = [
        event
        async for event in client.send_message(
            "hello",
            metadata={"authorization": "Bearer explicit-token"},
        )
    ]

    assert result == ["ok"]
    _, _, kwargs = fake_client.send_message_inputs[0]
    assert kwargs["request_metadata"]["authorization"] == "Bearer explicit-token"


@pytest.mark.asyncio
async def test_send_message_maps_jsonrpc_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rpc_error = JSONRPCErrorResponse(
        error=JSONRPCError(code=-32601, message="Unsupported method: message/send"),
        id="req-1",
    )
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(fail=A2AClientJSONRPCError(rpc_error))
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )
    with pytest.raises(
        A2AUnsupportedOperationError,
        match="Unsupported method",
    ):
        async for _event in client.send_message("hello"):
            raise AssertionError


@pytest.mark.asyncio
async def test_get_task_maps_transport_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(fail=A2AClientHTTPError(404, "gone"))
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    with pytest.raises(A2AUnsupportedOperationError, match="not supported"):
        await client.get_task("task-id")


@pytest.mark.asyncio
async def test_resubscribe_forward_events(monkeypatch: pytest.MonkeyPatch) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(events=[1, 2])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )
    result = [event async for event in client.resubscribe_task("task-id")]
    assert result == [1, 2]


@pytest.mark.asyncio
async def test_close_releases_owned_http_client() -> None:
    owned_http_client = AsyncMock(spec=httpx.AsyncClient)
    client = A2AClient("http://agent.example.com")
    client._httpx_client = owned_http_client
    client._owns_httpx_client = True
    client._client = object()
    await client.close()

    owned_http_client.aclose.assert_awaited_once()
    assert client._client is None


@pytest.mark.asyncio
async def test_close_preserves_borrowed_http_client() -> None:
    borrowed_http_client = AsyncMock(spec=httpx.AsyncClient)
    client = A2AClient("http://agent.example.com", httpx_client=borrowed_http_client)
    client._client = object()

    await client.close()

    borrowed_http_client.aclose.assert_not_awaited()
    assert client._client is None

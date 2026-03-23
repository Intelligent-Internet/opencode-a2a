from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.client import ClientConfig
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONError, A2AClientJSONRPCError
from a2a.types import (
    Artifact,
    JSONRPCError,
    JSONRPCErrorResponse,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TextPart,
)

from opencode_a2a.client import A2AClient
from opencode_a2a.client import client as client_module
from opencode_a2a.client.config import A2AClientSettings
from opencode_a2a.client.errors import (
    A2AAgentUnavailableError,
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2AUnsupportedOperationError,
)


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
            bearer_token="peer-token",
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

        def create(
            self,
            _card: object,
            consumers: list[object] | None = None,
            interceptors: list[object] | None = None,
            extensions: list[str] | None = None,
        ) -> _FakeClient:
            factory_calls["create_consumers"] = consumers
            factory_calls["interceptors"] = interceptors
            factory_calls["extensions"] = extensions
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
    assert factory_calls["interceptors"] is not None
    assert len(factory_calls["interceptors"]) == 1
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
    assert kwargs["request_metadata"] is None
    assert kwargs["context"] is None


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
            metadata={"authorization": "Bearer explicit-token", "trace_id": "trace-1"},
        )
    ]

    assert result == ["ok"]
    _, _, kwargs = fake_client.send_message_inputs[0]
    assert kwargs["request_metadata"] == {"trace_id": "trace-1"}
    assert kwargs["context"].state["headers"]["Authorization"] == "Bearer explicit-token"


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


def test_extract_text_prefers_stream_artifact_payload() -> None:
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(state=TaskState.working),
    )
    update = TaskArtifactUpdateEvent(
        task_id="remote-task",
        context_id="remote-context",
        artifact=Artifact(
            artifact_id="artifact-1",
            name="response",
            parts=[Part(root=TextPart(text="streamed remote text"))],
        ),
    )

    assert A2AClient.extract_text((task, update)) == "streamed remote text"


def test_extract_text_reads_task_status_message() -> None:
    task = Task(
        id="remote-task",
        context_id="remote-context",
        status=TaskStatus(
            state=TaskState.completed,
            message=Message(
                role=Role.agent,
                message_id="m1",
                parts=[Part(root=TextPart(text="status message text"))],
            ),
        ),
    )

    assert A2AClient.extract_text(task) == "status message text"


def test_extract_text_reads_nested_mapping_payload() -> None:
    payload = {
        "result": {
            "history": [
                {"parts": [{"text": "mapped nested text"}]},
            ]
        }
    }

    assert A2AClient.extract_text(payload) == "mapped nested text"


def test_extract_text_reads_model_dump_payload() -> None:
    class _Payload:
        def model_dump(self) -> dict[str, object]:
            return {"artifacts": [{"parts": [{"text": "model dump text"}]}]}

    assert A2AClient.extract_text(_Payload()) == "model dump text"


def test_extract_text_reads_direct_string_payload() -> None:
    assert A2AClient.extract_text("  string payload  ") == "string payload"


def test_extract_text_reads_message_and_artifact_attributes() -> None:
    class _ArtifactHolder:
        artifact = {"parts": [{"text": "artifact attribute text"}]}

    class _MessageHolder:
        message = {"parts": [{"text": "message attribute text"}]}

    assert A2AClient.extract_text(_ArtifactHolder()) == "artifact attribute text"
    assert A2AClient.extract_text(_MessageHolder()) == "message attribute text"


def test_extract_text_reads_result_history_and_artifacts_attributes() -> None:
    class _ResultHolder:
        result = {"parts": [{"text": "result attribute text"}]}

    class _HistoryHolder:
        history = [{"parts": [{"text": "history attribute text"}]}]

    class _Artifact:
        parts = [{"text": "artifacts attribute text"}]

    class _ArtifactsHolder:
        artifacts = [_Artifact()]

    assert A2AClient.extract_text(_ResultHolder()) == "result attribute text"
    assert A2AClient.extract_text(_HistoryHolder()) == "history attribute text"
    assert A2AClient.extract_text(_ArtifactsHolder()) == "artifacts attribute text"


@pytest.mark.asyncio
async def test_get_agent_card_maps_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenResolver:
        async def get_agent_card(self, **_kwargs: object) -> object:
            raise A2AClientJSONError("invalid json")

    async def _build_card_resolver(self: A2AClient) -> _BrokenResolver:
        return _BrokenResolver()

    client = A2AClient("http://agent.example.com")
    monkeypatch.setattr(A2AClient, "_build_card_resolver", _build_card_resolver)

    with pytest.raises(A2APeerProtocolError, match="invalid json"):
        await client.get_agent_card()


@pytest.mark.asyncio
async def test_cancel_task_adds_bearer_token_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(bearer_token="peer-token"),
    )
    fake_client = _FakeClient()
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    await client.cancel_task("task-id")

    params, _ = fake_client.cancel_inputs[0]
    assert params.metadata == {}


@pytest.mark.asyncio
async def test_get_task_uses_authorization_header_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient()
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    await client.get_task(
        "task-id",
        metadata={"authorization": "Bearer explicit-token", "trace_id": "trace-1"},
    )

    params, kwargs = fake_client.task_inputs[0]
    assert params.metadata == {"trace_id": "trace-1"}
    assert kwargs["context"].state["headers"]["Authorization"] == "Bearer explicit-token"


@pytest.mark.asyncio
async def test_cancel_task_uses_authorization_header_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient()
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    await client.cancel_task(
        "task-id",
        metadata={"authorization": "Bearer explicit-token", "trace_id": "trace-1"},
    )

    params, kwargs = fake_client.cancel_inputs[0]
    assert params.metadata == {"trace_id": "trace-1"}
    assert kwargs["context"].state["headers"]["Authorization"] == "Bearer explicit-token"


def test_map_jsonrpc_error_variants() -> None:
    client = A2AClient("http://agent.example.com")

    invalid_params_error = A2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32602, message="bad params"),
            id="req-1",
        )
    )
    internal_error = A2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32603, message="internal"),
            id="req-2",
        )
    )
    generic_error = A2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32000, message="generic"),
            id="req-3",
        )
    )

    mapped_invalid = client._map_jsonrpc_error(invalid_params_error)
    mapped_internal = client._map_jsonrpc_error(internal_error)
    mapped_generic = client._map_jsonrpc_error(generic_error)

    assert isinstance(mapped_invalid, A2APeerProtocolError)
    assert mapped_invalid.error_code == "invalid_params"
    assert isinstance(mapped_internal, A2AClientResetRequiredError)
    assert isinstance(mapped_generic, A2APeerProtocolError)
    assert mapped_generic.error_code == "peer_protocol_error"


def test_map_http_error_variants() -> None:
    client = A2AClient("http://agent.example.com")

    unsupported = client._map_http_error("message/send", A2AClientHTTPError(405, "nope"))
    reset = client._map_http_error("message/send", A2AClientHTTPError(503, "busy"))
    unavailable = client._map_http_error("message/send", A2AClientHTTPError(500, "boom"))

    assert isinstance(unsupported, A2AUnsupportedOperationError)
    assert unsupported.http_status == 405
    assert isinstance(reset, A2AClientResetRequiredError)
    assert reset.http_status == 503
    assert isinstance(unavailable, A2AAgentUnavailableError)


@pytest.mark.asyncio
async def test_build_card_resolver_requires_absolute_url() -> None:
    client = A2AClient("/relative/path")

    with pytest.raises(ValueError, match="absolute URL"):
        await client._build_card_resolver()


def test_split_request_metadata_and_resolver_headers() -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(bearer_token="peer-token", card_fetch_timeout=7),
    )

    request_metadata, extra_headers = client._split_request_metadata(
        {"authorization": "Bearer explicit-token", "trace_id": "trace-1"}
    )

    assert request_metadata == {"trace_id": "trace-1"}
    assert extra_headers == {"Authorization": "Bearer explicit-token"}
    assert client._build_default_headers() == {"Authorization": "Bearer peer-token"}
    assert client._build_resolver_http_kwargs() == {
        "timeout": 7,
        "headers": {"Authorization": "Bearer peer-token"},
    }


@pytest.mark.asyncio
async def test_header_interceptor_merges_static_and_dynamic_headers() -> None:
    interceptor = client_module._HeaderInterceptor({"Authorization": "Bearer peer-token"})
    context = client_module.ClientCallContext(state={"headers": {"X-Trace-Id": "trace-1"}})

    request_payload, http_kwargs = await interceptor.intercept(
        "message/send",
        {"jsonrpc": "2.0"},
        {"headers": {"Accept": "application/json"}},
        agent_card=None,
        context=context,
    )

    assert request_payload == {"jsonrpc": "2.0"}
    assert http_kwargs["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer peer-token",
        "X-Trace-Id": "trace-1",
    }


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
async def test_resubscribe_uses_authorization_header_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(events=[1])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(
        A2AClient,
        "_build_card_resolver",
        AsyncMock(return_value=_FakeCardResolver("card")),
    )

    result = [
        event
        async for event in client.resubscribe_task(
            "task-id",
            metadata={"authorization": "Bearer explicit-token", "trace_id": "trace-1"},
        )
    ]

    assert result == [1]
    params, kwargs = fake_client.resubscribe_inputs[0]
    assert params.metadata == {"trace_id": "trace-1"}
    assert kwargs["context"].state["headers"]["Authorization"] == "Bearer explicit-token"


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

from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.client import ClientConfig
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONError, A2AClientJSONRPCError
from a2a.types import JSONRPCError, JSONRPCErrorResponse, Task, TaskState, TaskStatus

from opencode_a2a.client import A2AClient
from opencode_a2a.client import client as client_module
from opencode_a2a.client.config import A2AClientSettings
from opencode_a2a.client.errors import (
    A2APeerProtocolError,
    A2ATimeoutError,
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
        task_results: list[object] | None = None,
        task_fail: BaseException | None = None,
    ):
        self._events = list(events or [])
        self._fail = fail
        self._task_results = list(task_results or [])
        self._task_fail = task_fail
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
        if self._task_fail:
            raise self._task_fail
        if self._fail:
            raise self._fail
        if self._task_results:
            return self._task_results.pop(0)
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


def _task(task_id: str, state: TaskState) -> Task:
    return Task(
        id=task_id,
        context_id="ctx-1",
        status=TaskStatus(state=state),
    )


@pytest.mark.asyncio
async def test_get_agent_card_cached_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = _FakeCardResolver("agent-card")

    client = A2AClient("http://agent.example.com")
    monkeypatch.setattr(client_module, "build_agent_card_resolver", lambda *_args: resolver)
    first = await client.get_agent_card()
    second = await client.get_agent_card()
    assert first == second == "agent-card"
    assert resolver.get_calls == 1


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

    monkeypatch.setattr(client_module, "ClientFactory", _FakeFactory)
    monkeypatch.setattr(
        client_module,
        "build_agent_card_resolver",
        lambda *_args: _FakeCardResolver("agent-card"),
    )
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
    response = await client.send("hello")
    assert response == "last"


@pytest.mark.asyncio
async def test_send_polling_fallback_returns_terminal_task(monkeypatch: pytest.MonkeyPatch) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(
            polling_fallback_enabled=True,
            polling_fallback_initial_interval_seconds=0.1,
            polling_fallback_max_interval_seconds=0.2,
            polling_fallback_backoff_multiplier=2.0,
            polling_fallback_timeout_seconds=5.0,
        ),
    )
    fake_client = _FakeClient(
        events=[(_task("task-1", TaskState.working), None)],
        task_results=[
            _task("task-1", TaskState.working),
            _task("task-1", TaskState.completed),
        ],
    )
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(client, "_sleep", _fake_sleep)

    response = await client.send("hello")

    assert response == (_task("task-1", TaskState.completed), None)
    assert [params.id for params, _kwargs in fake_client.task_inputs] == ["task-1", "task-1"]
    assert sleep_calls == [0.1, 0.2]


@pytest.mark.asyncio
async def test_send_polling_fallback_skips_input_required(monkeypatch: pytest.MonkeyPatch) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(polling_fallback_enabled=True),
    )
    event = (_task("task-1", TaskState.input_required), None)
    fake_client = _FakeClient(events=[event])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))

    response = await client.send("hello")

    assert response == event
    assert fake_client.task_inputs == []


@pytest.mark.asyncio
async def test_send_polling_fallback_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(
            polling_fallback_enabled=True,
            polling_fallback_initial_interval_seconds=0.1,
            polling_fallback_max_interval_seconds=0.2,
            polling_fallback_backoff_multiplier=2.0,
            polling_fallback_timeout_seconds=0.2,
        ),
    )
    fake_client = _FakeClient(
        events=[(_task("task-1", TaskState.working), None)],
        task_results=[_task("task-1", TaskState.working)],
    )
    now_values = iter([0.0, 0.0, 0.3])

    async def _fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(client, "_sleep", _fake_sleep)
    monkeypatch.setattr(client, "_current_time", lambda: next(now_values))

    with pytest.raises(A2ATimeoutError, match="polling fallback timed out"):
        await client.send("hello")


@pytest.mark.asyncio
async def test_send_polling_fallback_maps_get_task_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(
            polling_fallback_enabled=True,
            polling_fallback_initial_interval_seconds=0.1,
            polling_fallback_max_interval_seconds=0.2,
            polling_fallback_backoff_multiplier=2.0,
            polling_fallback_timeout_seconds=5.0,
        ),
    )
    fake_client = _FakeClient(
        events=[(_task("task-1", TaskState.working), None)],
        task_fail=A2AClientHTTPError(404, "gone"),
    )

    async def _fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(client, "_sleep", _fake_sleep)

    with pytest.raises(A2AUnsupportedOperationError, match="does not support tasks/get"):
        await client.send("hello")


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

    result = [event async for event in client.send_message("hello")]

    assert result == ["ok"]
    _, _, kwargs = fake_client.send_message_inputs[0]
    assert kwargs["request_metadata"] is None
    assert kwargs["context"] is not None
    assert kwargs["context"].state["headers"]["Authorization"] == "Bearer peer-token"


@pytest.mark.asyncio
async def test_send_message_adds_basic_auth_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(basic_auth="user:pass"),
    )
    fake_client = _FakeClient(events=["ok"])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))

    result = [event async for event in client.send_message("hello")]

    assert result == ["ok"]
    _, _, kwargs = fake_client.send_message_inputs[0]
    assert kwargs["request_metadata"] is None
    assert kwargs["context"] is not None
    assert kwargs["context"].state["headers"]["Authorization"] == (
        f"Basic {b64encode(b'user:pass').decode()}"
    )


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
async def test_send_message_prefers_explicit_authorization_without_default_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(events=["ok"])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))

    result = [
        event
        async for event in client.send_message(
            "hello", metadata={"authorization": "Bearer explicit-token"}
        )
    ]

    assert result == ["ok"]
    _, _, kwargs = fake_client.send_message_inputs[0]
    assert kwargs["request_metadata"] is None
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
    with pytest.raises(
        A2AUnsupportedOperationError,
        match="does not support the requested operation",
    ):
        async for _event in client.send_message("hello"):
            raise AssertionError


@pytest.mark.asyncio
async def test_get_agent_card_maps_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenResolver:
        async def get_agent_card(self, **_kwargs: object) -> object:
            raise A2AClientJSONError("invalid json")

    client = A2AClient("http://agent.example.com")
    monkeypatch.setattr(
        client_module,
        "build_agent_card_resolver",
        lambda *_args: _BrokenResolver(),
    )

    with pytest.raises(A2APeerProtocolError, match="invalid agent card payload"):
        await client.get_agent_card()


@pytest.mark.asyncio
async def test_get_agent_card_passes_basic_auth_to_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_http_kwargs: dict[str, object] = {}

    class _ResolverWithCapturedKwargs:
        async def get_agent_card(self, **kwargs: object) -> object:
            resolver_http_kwargs.update(kwargs)
            return "agent-card"

    client = A2AClient(
        "http://agent.example.com",
        settings=A2AClientSettings(card_fetch_timeout=7, basic_auth="user:pass"),
    )
    monkeypatch.setattr(
        client_module,
        "build_agent_card_resolver",
        lambda *_args: _ResolverWithCapturedKwargs(),
    )

    card = await client.get_agent_card()

    assert card == "agent-card"
    assert resolver_http_kwargs == {
        "http_kwargs": {
            "timeout": 7,
            "headers": {"Authorization": f"Basic {b64encode(b'user:pass').decode()}"},
        }
    }


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

    await client.cancel_task(
        "task-id",
        metadata={"authorization": "Bearer explicit-token", "trace_id": "trace-1"},
    )

    params, kwargs = fake_client.cancel_inputs[0]
    assert params.metadata == {"trace_id": "trace-1"}
    assert kwargs["context"].state["headers"]["Authorization"] == "Bearer explicit-token"


@pytest.mark.asyncio
async def test_get_task_maps_transport_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(fail=A2AClientHTTPError(404, "gone"))
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))

    with pytest.raises(A2AUnsupportedOperationError, match="does not support tasks/get"):
        await client.get_task("task-id")


@pytest.mark.asyncio
async def test_resubscribe_forward_events(monkeypatch: pytest.MonkeyPatch) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(events=[1, 2])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))
    result = [event async for event in client.resubscribe_task("task-id")]
    assert result == [1, 2]


@pytest.mark.asyncio
async def test_resubscribe_uses_authorization_header_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = A2AClient("http://agent.example.com")
    fake_client = _FakeClient(events=[1])
    monkeypatch.setattr(A2AClient, "_build_client", AsyncMock(return_value=fake_client))

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

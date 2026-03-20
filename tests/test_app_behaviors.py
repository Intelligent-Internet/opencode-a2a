from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.events import EventConsumer
from a2a.types import Task, TaskIdParams, TaskNotCancelableError, TaskState, TaskStatus
from a2a.utils.errors import ServerError
from fastapi import Request

import opencode_a2a_server.app as app_module
from opencode_a2a_server.app import (
    KeepaliveRESTAdapter,
    OpencodeRequestHandler,
    _build_agent_card_description,
    _build_chat_examples,
    _build_jsonrpc_extension_openapi_description,
    _build_jsonrpc_extension_openapi_examples,
    _build_rest_message_openapi_examples,
    _build_session_query_skill_examples,
    _configure_logging,
    _decode_payload_preview,
    _detect_sensitive_extension_method,
    _is_json_content_type,
    _looks_like_jsonrpc_envelope,
    _looks_like_jsonrpc_message_payload,
    _normalize_content_type,
    _normalize_log_level,
    _parse_content_length,
    _parse_json_body,
    _request_body_too_large_response,
    _RequestBodyTooLargeError,
    build_agent_card,
    create_app,
)
from opencode_a2a_server.extension_contracts import build_capability_snapshot
from opencode_a2a_server.runtime_profile import build_runtime_profile
from tests.helpers import DummyChatOpencodeClient, make_settings


def _request(path: str, body: bytes = b"{}") -> Request:
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    req = Request(scope, receive)
    req.state.user_identity = "opaque:test-id"
    return req


def test_request_payload_helpers_cover_edge_cases() -> None:
    assert _parse_json_body(b"{") is None
    assert _parse_json_body(b"[]") is None
    assert _parse_json_body(b'{"method":"message/send"}') == {"method": "message/send"}

    assert _detect_sensitive_extension_method(None) is None
    assert _detect_sensitive_extension_method({"method": "message/send"}) is None
    assert (
        _detect_sensitive_extension_method(
            {"method": app_module.SESSION_QUERY_METHODS["list_sessions"]}
        )
        == app_module.SESSION_QUERY_METHODS["list_sessions"]
    )

    assert _parse_content_length(None) is None
    assert _parse_content_length("invalid") is None
    assert _parse_content_length("-1") is None
    assert _parse_content_length("42") == 42

    assert _normalize_content_type(None) == ""
    assert _normalize_content_type("application/json; charset=utf-8") == "application/json"
    assert _is_json_content_type("") is False
    assert _is_json_content_type("application/json") is True
    assert _is_json_content_type("application/problem+json") is True
    assert _decode_payload_preview(b"abcdef", limit=3) == "abc...[truncated]"

    assert _looks_like_jsonrpc_message_payload(None) is False
    assert _looks_like_jsonrpc_message_payload({"message": {"parts": []}}) is True
    assert _looks_like_jsonrpc_message_payload({"message": {"role": "user"}}) is True
    assert _looks_like_jsonrpc_message_payload({"message": {"role": "ROLE_USER"}}) is False
    assert _looks_like_jsonrpc_envelope(None) is False
    assert _looks_like_jsonrpc_envelope({"jsonrpc": "2.0", "method": "message/send"}) is True
    assert _looks_like_jsonrpc_envelope({"jsonrpc": 2, "method": "message/send"}) is False

    response = _request_body_too_large_response(
        path="/",
        method="POST",
        error=_RequestBodyTooLargeError(limit=64, actual_size=65),
    )
    assert response.status_code == 413
    assert response.body == b'{"error":"Request body too large","max_bytes":64}'


def test_agent_card_helper_builders_cover_optional_branches() -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_project="alpha",
        a2a_allow_directory_override=False,
        a2a_enable_session_shell=True,
        opencode_workspace_root="/workspace",
        opencode_agent="planner",
        opencode_variant="fast",
    )

    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    disabled_capability_snapshot = build_capability_snapshot(
        runtime_profile=build_runtime_profile(
            make_settings(a2a_bearer_token="test-token", a2a_enable_session_shell=False)
        )
    )
    assert runtime_profile.summary_dict() == {
        "profile_id": "opencode-a2a-single-tenant-coding-v1",
        "deployment": {
            "id": "single_tenant_shared_workspace",
            "single_tenant": True,
            "shared_workspace_across_consumers": True,
            "tenant_isolation": "none",
        },
        "runtime_features": {
            "directory_binding": {
                "allow_override": False,
                "scope": "workspace_root_only",
                "metadata_field": "metadata.opencode.directory",
            },
            "session_shell": {
                "enabled": True,
                "availability": "enabled",
                "toggle": "A2A_ENABLE_SESSION_SHELL",
            },
            "service_features": {
                "streaming": {
                    "enabled": True,
                    "availability": "always",
                },
                "health_endpoint": {
                    "enabled": True,
                    "availability": "always",
                },
            },
        },
        "runtime_context": {
            "project": "alpha",
            "workspace_root": "/workspace",
            "agent": "planner",
            "variant": "fast",
        },
    }

    description = _build_agent_card_description(settings, runtime_profile)
    assert "Deployment project: alpha." in description
    assert "Workspace root: /workspace." in description
    assert any("project alpha" in item for item in _build_chat_examples("alpha"))
    assert all(
        "shell" not in item
        for item in _build_session_query_skill_examples(
            capability_snapshot=disabled_capability_snapshot
        )
    )
    assert any(
        "shell" in item
        for item in _build_session_query_skill_examples(capability_snapshot=capability_snapshot)
    )
    assert "opencode.sessions.shell" in _build_jsonrpc_extension_openapi_description(
        capability_snapshot=capability_snapshot
    )
    assert "session_shell" in _build_jsonrpc_extension_openapi_examples(
        capability_snapshot=capability_snapshot
    )
    assert "continue_session" in _build_rest_message_openapi_examples()


@pytest.mark.asyncio
async def test_auth_health_lifespan_and_openapi_cache(monkeypatch) -> None:
    class _ClosableClient(DummyChatOpencodeClient):
        def __init__(self, settings=None) -> None:
            super().__init__(settings)
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    closable = _ClosableClient(make_settings(a2a_bearer_token="test-token"))
    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: closable)

    settings = make_settings(a2a_bearer_token="test-token", a2a_enable_session_shell=True)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        agent_card = await client.get("/.well-known/agent-card.json")
        assert agent_card.status_code == 200

        unauthorized = await client.get("/health")
        assert unauthorized.status_code == 401

        wrong_token = await client.get("/health", headers={"Authorization": "Bearer wrong"})
        assert wrong_token.status_code == 401

        health = await client.get("/health", headers={"Authorization": "Bearer test-token"})
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "service": "opencode-a2a-server",
            "version": settings.a2a_version,
            "profile": {
                "profile_id": "opencode-a2a-single-tenant-coding-v1",
                "protocol_version": "0.3.0",
                "deployment": {
                    "id": "single_tenant_shared_workspace",
                    "single_tenant": True,
                    "shared_workspace_across_consumers": True,
                    "tenant_isolation": "none",
                },
                "runtime_features": {
                    "directory_binding": {
                        "allow_override": True,
                        "scope": "workspace_root_or_descendant",
                        "metadata_field": "metadata.opencode.directory",
                    },
                    "session_shell": {
                        "enabled": True,
                        "availability": "enabled",
                        "toggle": "A2A_ENABLE_SESSION_SHELL",
                    },
                    "service_features": {
                        "streaming": {
                            "enabled": True,
                            "availability": "always",
                        },
                        "health_endpoint": {
                            "enabled": True,
                            "availability": "always",
                        },
                    },
                },
            },
        }

    async with app.router.lifespan_context(app):
        pass
    assert closable.closed is True

    openapi_first = app.openapi()
    openapi_second = app.openapi()
    assert openapi_first is openapi_second
    root_examples = openapi_first["paths"]["/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert "session_shell" in root_examples
    assert "opencode.sessions.shell" in openapi_first["paths"]["/"]["post"]["description"]


@pytest.mark.asyncio
async def test_keepalive_rest_adapter_routes_and_preconsume_error() -> None:
    handler = MagicMock()
    adapter = KeepaliveRESTAdapter(
        agent_card=build_agent_card(make_settings(a2a_bearer_token="test-token")),
        http_handler=handler,
        sse_ping_seconds=12,
    )

    async def _stream(_request: Request, _context):  # noqa: ANN001
        yield {"id": "evt-1"}

    handler.on_resubscribe_to_task = _stream
    response = await adapter.routes()[("/v1/tasks/{id}:subscribe", "GET")](
        _request("/v1/tasks/x:subscribe")
    )
    assert response.ping_interval == 12

    class _BrokenRequest:
        async def body(self) -> bytes:
            raise ValueError("broken body")

    with pytest.raises(ServerError, match="Failed to pre-consume request body: broken body"):
        await adapter._handle_streaming_request(_stream, _BrokenRequest())


@pytest.mark.asyncio
async def test_on_cancel_task_and_resubscribe_cover_race_paths(monkeypatch) -> None:
    task_store = MagicMock()
    handler = OpencodeRequestHandler(agent_executor=MagicMock(), task_store=task_store)
    params = TaskIdParams(id="task-1")
    canceled_task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.canceled),
    )
    working_task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.working),
    )
    completed_task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.completed),
    )

    task_store.get = AsyncMock(return_value=None)
    with pytest.raises(ServerError):
        await handler.on_cancel_task(params)

    task_store.get = AsyncMock(return_value=canceled_task)
    assert await handler.on_cancel_task(params) is canceled_task

    task_store.get = AsyncMock(return_value=completed_task)
    with pytest.raises(ServerError):
        await handler.on_cancel_task(params)

    task_store.get = AsyncMock(side_effect=[working_task, canceled_task])

    async def _cancel_race(_self, _params, _context=None):  # noqa: ANN001
        raise ServerError(error=TaskNotCancelableError(message="already terminal"))

    monkeypatch.setattr(app_module.DefaultRequestHandler, "on_cancel_task", _cancel_race)
    assert await handler.on_cancel_task(params) is canceled_task

    task_store.get = AsyncMock(return_value=working_task)

    async def _super_cancel(_self, _params, _context=None):  # noqa: ANN001
        return working_task

    monkeypatch.setattr(app_module.DefaultRequestHandler, "on_cancel_task", _super_cancel)
    assert await handler.on_cancel_task(params) is working_task

    task_store.get = AsyncMock(return_value=None)
    with pytest.raises(ServerError):
        events = [item async for item in handler.on_resubscribe_to_task(params)]
        assert events == []

    task_store.get = AsyncMock(return_value=canceled_task)
    events = [item async for item in handler.on_resubscribe_to_task(params)]
    assert events == [canceled_task]

    task_store.get = AsyncMock(return_value=working_task)

    async def _super_resubscribe(_self, _params, _context=None):  # noqa: ANN001
        yield "evt-1"

    monkeypatch.setattr(
        app_module.DefaultRequestHandler,
        "on_resubscribe_to_task",
        _super_resubscribe,
    )
    events = [item async for item in handler.on_resubscribe_to_task(params)]
    assert events == ["evt-1"]


@pytest.mark.asyncio
async def test_on_message_send_covers_error_cleanup_and_internal_error(monkeypatch, caplog) -> None:
    class _Aggregator:
        def __init__(self, *, result=None, error: Exception | None = None) -> None:
            self._result = result
            self._error = error

        async def consume_and_break_on_interrupt(self, _consumer, *, blocking, event_callback):
            del blocking, event_callback
            if self._error is not None:
                raise self._error
            return self._result, False, None

    class _Handler(OpencodeRequestHandler):
        def __init__(self, aggregator: _Aggregator) -> None:
            super().__init__(agent_executor=MagicMock(), task_store=MagicMock())
            self.aggregator = aggregator
            self.queue = AsyncMock()
            self.producer = MagicMock()
            self.background_tasks: list[asyncio.Task] = []

        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            return (
                MagicMock(spec=EventConsumer),
                "task-1",
                self.queue,
                self.aggregator,
                self.producer,
            )

        async def _cleanup_producer(self, producer_task, task_id):  # noqa: ANN001
            del producer_task, task_id

        async def _send_push_notification_if_needed(self, task_id, result_aggregator):  # noqa: ANN001
            del task_id, result_aggregator

        def _track_background_task(self, task):  # noqa: ANN001
            self.background_tasks.append(task)

        def _validate_task_id_match(self, expected_task_id, actual_task_id):  # noqa: ANN001
            assert expected_task_id == actual_task_id

    params = types.SimpleNamespace(configuration=None)
    successful_result = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.completed),
    )

    error_handler = _Handler(_Aggregator(error=RuntimeError("boom")))
    with caplog.at_level("ERROR", logger="opencode_a2a_server.app"):
        with pytest.raises(RuntimeError, match="boom"):
            await error_handler.on_message_send(params)
    assert any("Agent execution failed" in record.message for record in caplog.records)

    canceled_handler = _Handler(_Aggregator(result=successful_result))

    class _CanceledTask:
        def cancelled(self) -> bool:
            return True

    monkeypatch.setattr(app_module.asyncio, "current_task", lambda: _CanceledTask())
    result = await canceled_handler.on_message_send(params)
    assert result is successful_result
    canceled_handler.producer.cancel.assert_called_once()
    canceled_handler.queue.close.assert_awaited_once_with(immediate=True)

    shield_handler = _Handler(_Aggregator(result=successful_result))
    monkeypatch.setattr(
        app_module.asyncio,
        "current_task",
        lambda: types.SimpleNamespace(cancelled=lambda: False),
    )

    async def _raise_cancelled(_awaitable):
        close = getattr(_awaitable, "close", None)
        if callable(close):
            close()
        raise asyncio.CancelledError

    monkeypatch.setattr(app_module.asyncio, "shield", _raise_cancelled)
    result = await shield_handler.on_message_send(params)
    assert result is successful_result

    internal_error_handler = _Handler(_Aggregator(result=None))
    with pytest.raises(ServerError):
        await internal_error_handler.on_message_send(params)


@pytest.mark.asyncio
async def test_on_message_send_non_blocking_tracks_background_work(monkeypatch) -> None:
    import a2a.utils.task as task_module

    class _Aggregator:
        def __init__(self, result: Task, bg_task: asyncio.Task[None]) -> None:
            self.result = result
            self.bg_task = bg_task

        async def consume_and_break_on_interrupt(self, _consumer, *, blocking, event_callback):
            assert blocking is False
            await event_callback()
            return self.result, True, self.bg_task

    class _Handler(OpencodeRequestHandler):
        def __init__(self, aggregator: _Aggregator) -> None:
            super().__init__(agent_executor=MagicMock(), task_store=MagicMock())
            self.aggregator = aggregator
            self.queue = AsyncMock()
            self.producer = MagicMock()
            self.background_tasks: list[asyncio.Task] = []

        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            return (
                MagicMock(spec=EventConsumer),
                "task-1",
                self.queue,
                self.aggregator,
                self.producer,
            )

        async def _cleanup_producer(self, producer_task, task_id):  # noqa: ANN001
            del producer_task, task_id

        async def _send_push_notification_if_needed(self, task_id, result_aggregator):  # noqa: ANN001
            del task_id, result_aggregator

        def _track_background_task(self, task):  # noqa: ANN001
            self.background_tasks.append(task)

        def _validate_task_id_match(self, expected_task_id, actual_task_id):  # noqa: ANN001
            assert expected_task_id == actual_task_id

    result = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.completed),
    )
    bg_task = asyncio.create_task(asyncio.sleep(0))
    handler = _Handler(_Aggregator(result=result, bg_task=bg_task))
    applied: dict[str, int] = {}

    def _apply_history_length(task: Task, history_length: int) -> Task:
        applied["history_length"] = history_length
        return task

    monkeypatch.setattr(task_module, "apply_history_length", _apply_history_length)

    params = types.SimpleNamespace(
        configuration=types.SimpleNamespace(blocking=False, history_length=7)
    )
    returned = await handler.on_message_send(params)
    assert returned == result
    assert applied["history_length"] == 7
    assert len(handler.background_tasks) == 2
    assert {task.get_name() for task in handler.background_tasks} == {
        "continue_consuming:task-1",
        "cleanup_producer:task-1",
    }
    await asyncio.gather(*handler.background_tasks, return_exceptions=True)


def test_normalize_log_level_configure_logging_and_main(monkeypatch) -> None:
    assert _normalize_log_level("debug") == "DEBUG"

    basic_config_calls: list[dict[str, object]] = []
    uvicorn_error_logger = MagicMock()
    uvicorn_access_logger = MagicMock()

    def _fake_get_logger(name: str | None = None) -> MagicMock:
        if name is None:
            return MagicMock()
        if name == "uvicorn.error":
            return uvicorn_error_logger
        if name == "uvicorn.access":
            return uvicorn_access_logger
        raise AssertionError(name)

    monkeypatch.setattr(
        app_module.logging,
        "basicConfig",
        lambda **kwargs: basic_config_calls.append(kwargs),
    )
    monkeypatch.setattr(app_module.logging, "getLogger", _fake_get_logger)

    _configure_logging("INFO")
    assert basic_config_calls[0]["level"] == app_module.logging.INFO
    uvicorn_error_logger.setLevel.assert_called_once_with("INFO")
    uvicorn_access_logger.setLevel.assert_called_once_with("INFO")

    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_log_level="debug",
        a2a_host="127.0.0.1",
        a2a_port=9001,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(app_module.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(app_module, "create_app", lambda _settings: "app-object")
    monkeypatch.setattr(
        app_module,
        "_configure_logging",
        lambda level: captured.setdefault("level", level),
    )
    monkeypatch.setattr(
        app_module.uvicorn,
        "run",
        lambda app, host, port, log_level: captured.update(
            {"app": app, "host": host, "port": port, "log_level": log_level}
        ),
    )

    app_module.main()

    assert captured == {
        "level": "DEBUG",
        "app": "app-object",
        "host": "127.0.0.1",
        "port": 9001,
        "log_level": "debug",
    }

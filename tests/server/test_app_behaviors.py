from __future__ import annotations

import asyncio
import json
import logging
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.events import EventConsumer
from a2a.server.routes.rest_dispatcher import RestDispatcher
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    InternalError,
    Message,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    UnsupportedOperationError,
)
from a2a.utils.errors import InvalidRequestError
from fastapi import Request
from google.protobuf.json_format import MessageToDict, ParseError

import opencode_a2a.server.application as app_module
from opencode_a2a.contracts.extensions import build_capability_snapshot
from opencode_a2a.profile.runtime import build_runtime_profile
from opencode_a2a.server.application import (
    OpencodeRequestHandler,
    _build_agent_card_description,
    _build_chat_examples,
    _build_jsonrpc_extension_openapi_description,
    _build_jsonrpc_extension_openapi_examples,
    _build_rest_message_openapi_examples,
    _build_session_management_skill_examples,
    _configure_logging,
    _decode_payload_preview,
    _detect_sensitive_extension_method,
    _is_json_content_type,
    _looks_like_jsonrpc_envelope,
    _normalize_content_type,
    _normalize_log_level,
    _parse_content_length,
    _parse_json_body,
    _parse_rest_send_message_request,
    _request_body_too_large_response,
    _RequestBodyTooLargeError,
    _rest_error_response,
    create_app,
)
from opencode_a2a.server.task_store import TaskStoreOperationError
from tests.support.helpers import (
    DummyChatOpencodeUpstreamClient,
    make_basic_auth_header,
    make_settings,
)


def _agent_card() -> AgentCard:
    return AgentCard(name="opencode-a2a", capabilities=AgentCapabilities(streaming=True))


def _request(
    path: str,
    body: bytes = b"{}",
    *,
    method: str = "POST",
    path_params: dict[str, str] | None = None,
) -> Request:
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
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "path_params": path_params or {},
        "state": {},
    }
    req = Request(scope, receive)
    req.state.user_identity = "opaque:test-id"
    return req


def test_request_payload_helpers_cover_edge_cases() -> None:
    assert _parse_json_body(b"{") is None
    assert _parse_json_body(b"[]") is None
    assert _parse_json_body(b'{"method":"SendMessage"}') == {"method": "SendMessage"}

    assert _detect_sensitive_extension_method(None) is None
    assert _detect_sensitive_extension_method({"method": "SendMessage"}) is None
    assert (
        _detect_sensitive_extension_method({"method": app_module.SESSION_METHODS["list_sessions"]})
        == app_module.SESSION_METHODS["list_sessions"]
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

    assert _looks_like_jsonrpc_envelope(None) is False
    assert _looks_like_jsonrpc_envelope({"jsonrpc": "2.0", "method": "SendMessage"}) is True
    assert _looks_like_jsonrpc_envelope({"jsonrpc": 2, "method": "SendMessage"}) is False

    response = _request_body_too_large_response(
        path="/",
        method="POST",
        error=_RequestBodyTooLargeError(limit=64, actual_size=65),
    )
    assert response.status_code == 413
    payload = json.loads(response.body)
    assert payload["error"]["code"] == 413
    assert payload["error"]["status"] == "RESOURCE_EXHAUSTED"
    assert payload["error"]["message"] == "Request body too large"
    assert payload["error"]["details"][0] == {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "REQUEST_BODY_TOO_LARGE",
        "domain": "a2a-protocol.org",
        "metadata": {"maxBytes": "64", "actualSize": "65"},
    }
    assert payload["error"]["details"][1] == {
        "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
        "maxBytes": 64,
        "actualSize": 65,
    }


def test_rest_message_parsing_helpers_cover_upgrade_paths() -> None:
    request_v1 = _request("/v1/message:send")
    request_v1.state.a2a_protocol_version = "1.0"
    v1_error = _rest_error_response(
        request=request_v1,
        default_protocol_version="1.0",
        error=InvalidRequestError(message="bad payload", data={"path": "/v1/message:send"}),
    )
    assert v1_error.status_code == 400
    assert json.loads(v1_error.body) == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "bad payload",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "INVALID_REQUEST",
                    "domain": "a2a-protocol.org",
                    "metadata": {"path": "/v1/message:send"},
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "path": "/v1/message:send",
                },
            ],
        }
    }

    request_default = _request("/v1/message:send")
    parse_error = _rest_error_response(
        request=request_default,
        default_protocol_version="1.0",
        error=ParseError("bad parse"),
    )
    assert parse_error.status_code == 400
    assert json.loads(parse_error.body) == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "bad parse",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "INVALID_REQUEST",
                    "domain": "a2a-protocol.org",
                }
            ],
        }
    }

    generic_error = _rest_error_response(
        request=request_default,
        default_protocol_version="1.0",
        error=RuntimeError("boom"),
    )
    assert generic_error.status_code == 500
    assert json.loads(generic_error.body) == {
        "error": {
            "code": 500,
            "status": "INTERNAL",
            "message": "unknown exception",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "INTERNAL_ERROR",
                    "domain": "a2a-protocol.org",
                }
            ],
        }
    }

    parsed = _parse_rest_send_message_request(
        json.dumps(
            {
                "message": {
                    "messageId": "msg-2",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello from rest"}],
                },
                "configuration": {"returnImmediately": True},
            }
        ).encode("utf-8")
    )
    assert isinstance(parsed, SendMessageRequest)
    assert MessageToDict(parsed) == {
        "message": {
            "messageId": "msg-2",
            "role": "ROLE_USER",
            "parts": [{"text": "hello from rest"}],
        },
        "configuration": {"returnImmediately": True},
    }
    with pytest.raises(InvalidRequestError, match="REST message payload must be a JSON object"):
        _parse_rest_send_message_request(b"[]")
    with pytest.raises(
        InvalidRequestError,
        match="REST message payload must use message.parts, not message.content.",
    ):
        _parse_rest_send_message_request(
            json.dumps(
                {
                    "message": {
                        "messageId": "msg-legacy",
                        "role": "ROLE_USER",
                        "content": [{"text": "hello from rest"}],
                    }
                }
            ).encode("utf-8")
        )
    with pytest.raises(
        InvalidRequestError,
        match="REST message payload must use ROLE_\\* values for message.role.",
    ):
        _parse_rest_send_message_request(
            json.dumps(
                {
                    "message": {
                        "messageId": "msg-legacy",
                        "role": "user",
                        "parts": [{"text": "hello from rest"}],
                    }
                }
            ).encode("utf-8")
        )
    with pytest.raises(
        InvalidRequestError,
        match="message.parts\\[0\\] must use direct Part fields such as text, raw, url, or data.",
    ):
        _parse_rest_send_message_request(
            json.dumps(
                {
                    "message": {
                        "messageId": "msg-legacy",
                        "role": "ROLE_USER",
                        "parts": [{"file": {"uri": "file:///tmp/report.txt"}}],
                    }
                }
            ).encode("utf-8")
        )


def test_agent_card_helper_builders_cover_optional_branches() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
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
            make_settings(test_bearer_token="test-token", a2a_enable_session_shell=False)
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
            "workspace_binding": {
                "enabled": True,
                "metadata_field": "metadata.opencode.workspace.id",
                "upstream_query_param": "workspace",
                "precedence": "prefer_workspace_else_directory",
            },
            "session_shell": {
                "enabled": True,
                "availability": "enabled",
                "toggle": "A2A_ENABLE_SESSION_SHELL",
            },
            "workspace_mutations": {
                "enabled": False,
                "availability": "disabled",
                "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
            },
            "execution_environment": {
                "sandbox": {
                    "mode": "unknown",
                    "filesystem_scope": "unknown",
                },
                "network": {
                    "access": "unknown",
                },
                "approval": {
                    "policy": "unknown",
                    "escalation_behavior": "unknown",
                },
                "write_access": {
                    "scope": "unknown",
                    "outside_workspace": "unknown",
                },
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

    public_description = _build_agent_card_description(
        settings,
        runtime_profile,
        include_detailed_contracts=False,
    )
    assert "Deployment project: alpha." in public_description
    assert "Workspace root: /workspace." not in public_description
    assert "authenticated extended Agent Card discovery" in public_description

    extended_description = _build_agent_card_description(
        settings,
        runtime_profile,
        include_detailed_contracts=True,
    )
    assert "Deployment project: alpha." in extended_description
    assert "Workspace root: /workspace." in extended_description
    assert "currently return unsupported" in extended_description
    assert any("project alpha" in item for item in _build_chat_examples("alpha"))
    assert all(
        "shell" not in item
        for item in _build_session_management_skill_examples(
            capability_snapshot=disabled_capability_snapshot
        )
    )
    assert any(
        "shell" in item
        for item in _build_session_management_skill_examples(
            capability_snapshot=capability_snapshot
        )
    )
    assert "opencode.sessions.shell" in _build_jsonrpc_extension_openapi_description(
        capability_snapshot=capability_snapshot
    )
    assert "session_shell" in _build_jsonrpc_extension_openapi_examples(
        capability_snapshot=capability_snapshot
    )
    assert "worktrees_create" not in _build_jsonrpc_extension_openapi_examples(
        capability_snapshot=capability_snapshot
    )
    assert "continue_session" in _build_rest_message_openapi_examples()


@pytest.mark.asyncio
async def test_health_endpoint_accepts_configured_basic_auth(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    settings = make_settings(
        test_bearer_token="test-token",
        test_basic_username="operator",
        test_basic_password="op-pass",  # pragma: allowlist secret
    )
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health", headers=make_basic_auth_header("operator", "op-pass"))

    assert health.status_code == 200


@pytest.mark.asyncio
async def test_health_endpoint_rejects_disabled_registry_token(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    settings = make_settings(
        test_bearer_token=None,
        a2a_static_auth_credentials=(
            {
                "scheme": "bearer",
                "token": "token-enabled",
                "principal": "automation-enabled",
            },
            {
                "scheme": "bearer",
                "token": "token-disabled",
                "principal": "automation-disabled",
                "enabled": False,
            },
        ),
    )
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.get("/health", headers={"Authorization": "Bearer token-disabled"})
        accepted = await client.get("/health", headers={"Authorization": "Bearer token-enabled"})

    assert rejected.status_code == 401
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_registry_auth_accepts_configured_bearer_only(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    settings = make_settings(
        a2a_static_auth_credentials=(
            {
                "scheme": "bearer",
                "token": "token-enabled",
                "principal": "automation-enabled",
            },
        ),
    )
    app = app_module.create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        registry = await client.get("/health", headers={"Authorization": "Bearer token-enabled"})

    assert registry.status_code == 200


@pytest.mark.asyncio
async def test_auth_health_lifespan_and_openapi_cache(monkeypatch, caplog) -> None:
    class _ClosableClient(DummyChatOpencodeUpstreamClient):
        def __init__(self, settings=None) -> None:
            super().__init__(settings)
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    closable = _ClosableClient(make_settings(test_bearer_token="test-token"))
    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: closable)

    settings = make_settings(test_bearer_token="test-token", a2a_enable_session_shell=True)
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
            "service": "opencode-a2a",
            "version": settings.a2a_version,
            "profile": {
                "profile_id": "opencode-a2a-single-tenant-coding-v1",
                "protocol_version": settings.a2a_protocol_version,
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
                    "workspace_binding": {
                        "enabled": True,
                        "metadata_field": "metadata.opencode.workspace.id",
                        "upstream_query_param": "workspace",
                        "precedence": "prefer_workspace_else_directory",
                    },
                    "session_shell": {
                        "enabled": True,
                        "availability": "enabled",
                        "toggle": "A2A_ENABLE_SESSION_SHELL",
                    },
                    "workspace_mutations": {
                        "enabled": False,
                        "availability": "disabled",
                        "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
                    },
                    "execution_environment": {
                        "sandbox": {
                            "mode": "unknown",
                            "filesystem_scope": "unknown",
                        },
                        "network": {
                            "access": "unknown",
                        },
                        "approval": {
                            "policy": "unknown",
                            "escalation_behavior": "unknown",
                        },
                        "write_access": {
                            "scope": "unknown",
                            "outside_workspace": "unknown",
                        },
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

    with caplog.at_level(logging.INFO, logger="opencode_a2a.server.lifespan"):
        async with app.router.lifespan_context(app):
            pass
    assert closable.closed is True
    assert any(
        "Lightweight persistence configured" in record.message
        and "backend=database" in record.message
        and "scope=sdk_tasks_and_adapter_state" in record.message
        for record in caplog.records
    )

    openapi_first = app.openapi()
    openapi_second = app.openapi()
    assert openapi_first is openapi_second
    root_examples = openapi_first["paths"]["/"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert "session_shell" in root_examples
    assert "worktrees_create" not in root_examples
    assert "opencode.sessions.shell" in openapi_first["paths"]["/"]["post"]["description"]


@pytest.mark.asyncio
async def test_rest_adapter_routes_and_preconsume_error() -> None:
    handler = MagicMock()
    adapter = RestDispatcher(request_handler=handler)

    async def _stream(_request: Request, _context):  # noqa: ANN001
        yield {"id": "evt-1"}

    handler.on_subscribe_to_task = _stream
    response = await adapter.on_subscribe_to_task(
        _request("/v1/tasks/x:subscribe", method="GET", path_params={"id": "x"})
    )
    assert response is not None

    class _BrokenRequest:
        async def body(self) -> bytes:
            raise ValueError("broken body")

    with pytest.raises(
        InvalidRequestError, match="Failed to pre-consume request body: broken body"
    ):
        await adapter._handle_streaming(  # pyright: ignore[reportAttributeAccessIssue]
            _BrokenRequest(),
            lambda _context: _stream(None, _context),
        )


@pytest.mark.asyncio
async def test_push_notification_routes_are_explicitly_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummyChatOpencodeUpstreamClient,
    )
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/tasks/task-1/pushNotificationConfigs",
            headers={"Authorization": "Bearer test-token"},
            json={"pushNotificationConfig": {"url": "https://example.com/hook"}},
        )

    assert response.status_code == 501
    assert response.json() == {
        "error": {
            "code": 501,
            "status": "UNIMPLEMENTED",
            "message": "Push notifications are not supported by the agent",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "PUSH_NOTIFICATIONS_UNSUPPORTED",
                    "domain": "a2a-protocol.org",
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_push_notification_jsonrpc_methods_remain_unsupported(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummyChatOpencodeUpstreamClient,
    )
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "GetTaskPushNotificationConfig",
                "params": {"id": "task-1"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "error": {
            "code": -32004,
            "message": "This operation is not supported",
            "data": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "UNSUPPORTED_OPERATION",
                    "domain": "a2a-protocol.org",
                }
            ],
        },
        "id": 1,
        "jsonrpc": "2.0",
    }


@pytest.mark.asyncio
async def test_on_cancel_task_and_resubscribe_cover_race_paths(monkeypatch) -> None:
    task_store = MagicMock()
    handler = OpencodeRequestHandler(
        agent_executor=MagicMock(),
        task_store=task_store,
        agent_card=_agent_card(),
    )
    handler.agent_executor.cancel = AsyncMock()
    handler._queue_manager.tap = AsyncMock(return_value=MagicMock())  # noqa: SLF001
    cancel_params = CancelTaskRequest(id="task-1")
    subscribe_params = SubscribeToTaskRequest(id="task-1")
    canceled_task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
    )
    working_task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    completed_task = Task(
        id="task-1",
        context_id="ctx-1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )

    task_store.get = AsyncMock(return_value=None)
    with pytest.raises(TaskNotFoundError):
        await handler.on_cancel_task(cancel_params)

    task_store.get = AsyncMock(return_value=canceled_task)
    assert await handler.on_cancel_task(cancel_params) is canceled_task

    task_store.get = AsyncMock(return_value=completed_task)
    with pytest.raises(TaskNotCancelableError):
        await handler.on_cancel_task(cancel_params)

    task_store.get = AsyncMock(side_effect=[working_task, canceled_task])

    async def _consume_non_canceled(_self, _consumer):  # noqa: ANN001
        return working_task

    monkeypatch.setattr(app_module.ResultAggregator, "consume_all", _consume_non_canceled)
    assert await handler.on_cancel_task(cancel_params) is canceled_task

    task_store.get = AsyncMock(return_value=working_task)

    async def _consume_canceled(_self, _consumer):  # noqa: ANN001
        return canceled_task

    monkeypatch.setattr(app_module.ResultAggregator, "consume_all", _consume_canceled)
    assert await handler.on_cancel_task(cancel_params) is canceled_task

    task_store.get = AsyncMock(return_value=None)
    with pytest.raises(TaskNotFoundError):
        events = [item async for item in handler.on_subscribe_to_task(subscribe_params)]
        assert events == []

    task_store.get = AsyncMock(return_value=canceled_task)
    events = [item async for item in handler.on_subscribe_to_task(subscribe_params)]
    assert events == [canceled_task]

    task_store.get = AsyncMock(return_value=working_task)

    async def _consume_and_emit(_self, _consumer):  # noqa: ANN001
        yield "evt-1"

    monkeypatch.setattr(
        app_module.ResultAggregator,
        "consume_and_emit",
        _consume_and_emit,
    )
    events = [item async for item in handler.on_subscribe_to_task(subscribe_params)]
    assert events == [working_task, "evt-1"]


@pytest.mark.asyncio
async def test_rest_message_routes_cover_message_and_error_wrappers(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummyChatOpencodeUpstreamClient,
    )
    app = create_app(make_settings(test_bearer_token="test-token"))
    handler = app.state._jsonrpc_app._http_handler  # noqa: SLF001

    async def _message_response(params, context=None):  # noqa: ANN001
        del params, context
        return Message(
            message_id="m-server",
            role=app_module.Role.ROLE_AGENT,
            parts=[app_module.Part(text="server reply")],
        )

    async def _stream_failure(params, context=None):  # noqa: ANN001
        del params, context
        if False:  # pragma: no cover
            yield None
        raise InvalidRequestError(message="stream bad")

    handler.on_message_send = _message_response
    handler.on_message_send_stream = _stream_failure

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        }
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        send_response = await client.post("/v1/message:send", headers=headers, json=payload)
        stream_response = await client.post("/v1/message:stream", headers=headers, json=payload)

    assert send_response.status_code == 200
    assert send_response.json() == {
        "message": {
            "messageId": "m-server",
            "role": "ROLE_AGENT",
            "parts": [{"text": "server reply"}],
        }
    }
    assert stream_response.status_code == 400
    assert stream_response.json() == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "stream bad",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "INVALID_REQUEST",
                    "domain": "a2a-protocol.org",
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_task_store_failures_map_to_stable_handler_errors() -> None:
    task_store = MagicMock()
    handler = OpencodeRequestHandler(
        agent_executor=MagicMock(),
        task_store=task_store,
        agent_card=_agent_card(),
    )

    task_store.get = AsyncMock(side_effect=TaskStoreOperationError("get", "task-1"))
    with pytest.raises(InternalError, match="Task store unavailable while loading task state."):
        await handler.on_get_task(GetTaskRequest(id="task-1"))

    with pytest.raises(InternalError, match="Task store unavailable while loading task state."):
        events = [
            item async for item in handler.on_subscribe_to_task(SubscribeToTaskRequest(id="task-1"))
        ]
        assert events == []


@pytest.mark.asyncio
async def test_on_message_send_returns_stable_failure_task_for_task_store_error() -> None:
    class _Aggregator:
        async def consume_and_break_on_interrupt(self, _consumer, *, blocking, event_callback):
            del _consumer, blocking, event_callback
            raise TaskStoreOperationError("save", "task-1")

    class _Handler(OpencodeRequestHandler):
        def __init__(self) -> None:
            super().__init__(
                agent_executor=MagicMock(),
                task_store=MagicMock(),
                agent_card=_agent_card(),
            )
            self.queue = AsyncMock()
            self.producer = MagicMock()

        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            return (
                MagicMock(spec=EventConsumer),
                "task-1",
                self.queue,
                _Aggregator(),
                self.producer,
            )

        async def _cleanup_producer(self, producer_task, task_id):  # noqa: ANN001
            del producer_task, task_id

        async def _send_push_notification_if_needed(self, task_id, result_aggregator):  # noqa: ANN001
            del task_id, result_aggregator

        def _track_background_task(self, task):  # noqa: ANN001
            task.cancel()

    params = types.SimpleNamespace(
        message=types.SimpleNamespace(contextId="ctx-1"), configuration=None
    )
    result = await _Handler().on_message_send(params)
    assert result.status.state == TaskState.TASK_STATE_FAILED
    assert result.metadata == {
        "opencode": {
            "error": {
                "type": app_module.TASK_STORE_ERROR_TYPE,
                "operation": "save",
            }
        }
    }


@pytest.mark.asyncio
async def test_on_message_send_stream_emits_stable_failure_events_for_task_store_error() -> None:
    class _Aggregator:
        async def consume_and_emit(self, _consumer):
            if _consumer is None:  # pragma: no cover
                yield None
            raise TaskStoreOperationError("save", "task-1")

    class _Handler(OpencodeRequestHandler):
        def __init__(self) -> None:
            super().__init__(
                agent_executor=MagicMock(),
                task_store=MagicMock(),
                agent_card=_agent_card(),
            )
            self.queue = AsyncMock()
            self.producer = MagicMock()
            self.background_tasks: list[asyncio.Task] = []

        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            return (
                MagicMock(spec=EventConsumer),
                "task-1",
                self.queue,
                _Aggregator(),
                self.producer,
            )

        async def _cleanup_producer(self, producer_task, task_id):  # noqa: ANN001
            del producer_task, task_id

        async def _send_push_notification_if_needed(self, task_id, result_aggregator):  # noqa: ANN001
            del task_id, result_aggregator

        def _track_background_task(self, task):  # noqa: ANN001
            self.background_tasks.append(task)

    params = types.SimpleNamespace(
        message=types.SimpleNamespace(contextId="ctx-1"), configuration=None
    )
    events = [event async for event in _Handler().on_message_send_stream(params)]

    assert len(events) == 2
    assert events[-1].status.state == TaskState.TASK_STATE_FAILED
    assert events[-1].metadata == {
        "opencode": {
            "error": {
                "type": app_module.TASK_STORE_ERROR_TYPE,
                "operation": "save",
            }
        }
    }


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
            super().__init__(
                agent_executor=MagicMock(),
                task_store=MagicMock(),
                agent_card=_agent_card(),
            )
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
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )

    error_handler = _Handler(_Aggregator(error=RuntimeError("boom")))
    with caplog.at_level("ERROR", logger="opencode_a2a.server.application"):
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
    with pytest.raises(InternalError):
        await internal_error_handler.on_message_send(params)


@pytest.mark.asyncio
async def test_on_message_send_non_blocking_tracks_background_work(monkeypatch) -> None:
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
            super().__init__(
                agent_executor=MagicMock(),
                task_store=MagicMock(),
                agent_card=_agent_card(),
            )
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
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )
    bg_task = asyncio.create_task(asyncio.sleep(0))
    handler = _Handler(_Aggregator(result=result, bg_task=bg_task))
    applied: dict[str, int] = {}

    def _apply_history_length(task: Task, configuration) -> Task:  # noqa: ANN001
        applied["history_length"] = configuration.history_length
        return task

    monkeypatch.setattr(app_module, "apply_history_length", _apply_history_length)

    params = types.SimpleNamespace(
        configuration=types.SimpleNamespace(return_immediately=True, history_length=7)
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


@pytest.mark.asyncio
async def test_on_message_send_rejects_output_modes_without_text_plain() -> None:
    class _Handler(OpencodeRequestHandler):
        def __init__(self) -> None:
            super().__init__(
                agent_executor=MagicMock(),
                task_store=MagicMock(),
                agent_card=_agent_card(),
            )
            self.setup_called = False

        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            self.setup_called = True
            raise AssertionError("_setup_message_execution should not be called")

    handler = _Handler()
    params = types.SimpleNamespace(
        configuration=types.SimpleNamespace(accepted_output_modes=["application/json"])
    )

    with pytest.raises(UnsupportedOperationError) as exc_info:
        await handler.on_message_send(params)

    assert "require text/plain" in exc_info.value.message
    assert exc_info.value.data == {
        "accepted_output_modes": ["application/json"],
        "required_output_modes": ["text/plain"],
        "supported_output_modes": ["text/plain", "application/json"],
    }
    assert handler.setup_called is False


@pytest.mark.asyncio
async def test_on_message_send_stream_rejects_incompatible_output_modes_before_execution() -> None:
    class _Handler(OpencodeRequestHandler):
        def __init__(self) -> None:
            super().__init__(
                agent_executor=MagicMock(),
                task_store=MagicMock(),
                agent_card=_agent_card(),
            )
            self.setup_called = False

        async def _setup_message_execution(self, params, context=None):  # noqa: ANN001
            del params, context
            self.setup_called = True
            raise AssertionError("_setup_message_execution should not be called")

    handler = _Handler()
    params = types.SimpleNamespace(
        configuration=types.SimpleNamespace(accepted_output_modes=["image/png"])
    )

    with pytest.raises(UnsupportedOperationError) as exc_info:
        await handler.on_message_send_stream(params).__anext__()

    assert "not compatible" in exc_info.value.message
    assert exc_info.value.data == {
        "accepted_output_modes": ["image/png"],
        "supported_output_modes": ["text/plain", "application/json"],
    }
    assert handler.setup_called is False


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
        test_bearer_token="test-token",
        a2a_log_level="debug",
        a2a_host="127.0.0.1",
        a2a_port=9001,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(app_module, "Settings", lambda: settings)
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

from __future__ import annotations

import json
import logging
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.context import ServerCallContext
from a2a.server.routes.rest_dispatcher import RestDispatcher
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    GetTaskRequest,
    InternalError,
    Message,
    Part,
    Role,
    SubscribeToTaskRequest,
    UnsupportedOperationError,
)
from a2a.utils.errors import InvalidRequestError
from fastapi import Request
from google.protobuf.json_format import ParseError

import opencode_a2a.server.application as app_module
from opencode_a2a.a2a_protocol import CORE_JSONRPC_METHODS
from opencode_a2a.contracts.extensions import (
    SESSION_METHODS,
    build_capability_snapshot,
)
from opencode_a2a.profile.runtime import build_runtime_profile
from opencode_a2a.protocol_versions import A2A_PROTOCOL_VERSION
from opencode_a2a.server.agent_card import (
    _build_agent_card_description,
    _build_chat_examples,
    _build_session_management_skill_examples,
)
from opencode_a2a.server.application import (
    OpencodeRequestHandler,
    _configure_logging,
    _normalize_log_level,
    _rest_error_response,
    create_app,
)
from opencode_a2a.server.openapi import (
    _build_jsonrpc_extension_openapi_description,
    _build_jsonrpc_extension_openapi_examples,
    _build_rest_message_openapi_examples,
)
from opencode_a2a.server.request_parsing import (
    _decode_payload_preview,
    _detect_sensitive_extension_method,
    _is_json_content_type,
    _looks_like_jsonrpc_envelope,
    _normalize_content_type,
    _parse_content_length,
    _parse_json_body,
    _request_body_too_large_response,
    _RequestBodyTooLargeError,
)
from opencode_a2a.server.task_store import TaskStoreOperationError
from tests.support.async_iterators import iter_async
from tests.support.helpers import (
    DummyChatOpencodeUpstreamClient,
    make_basic_auth_header,
)
from tests.support.settings import make_settings


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
        _detect_sensitive_extension_method({"method": SESSION_METHODS["list_sessions"]})
        == SESSION_METHODS["list_sessions"]
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
    request_v1 = _request("/message:send")
    request_v1.state.a2a_protocol_version = "1.0"
    v1_error = _rest_error_response(
        request=request_v1,
        error=InvalidRequestError(message="bad payload", data={"path": "/message:send"}),
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
                    "metadata": {"path": "/message:send"},
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "path": "/message:send",
                },
            ],
        }
    }

    request_default = _request("/message:send")
    parse_error = _rest_error_response(
        request=request_default,
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


def test_agent_card_helper_builders_cover_optional_branches() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_project="alpha",
        a2a_allow_directory_override=False,
        a2a_enable_session_shell=True,
        opencode_workspace_root="/workspace",
        opencode_agent="planner",
        opencode_variant="fast",
        a2a_expose_workspace_root_in_card=True,
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
                "metrics_endpoint": {
                    "enabled": True,
                    "availability": "enabled",
                    "path": "/metrics",
                    "authentication": "required",
                    "toggle": "A2A_METRICS_ENABLED",
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
    assert "Workspace root" not in extended_description
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
    jsonrpc_description = _build_jsonrpc_extension_openapi_description()
    assert "authenticated extended Agent Card" in jsonrpc_description
    assert "opencode.sessions.shell" not in jsonrpc_description
    assert all(method in jsonrpc_description for method in CORE_JSONRPC_METHODS)
    assert "message_send_session_binding" in _build_jsonrpc_extension_openapi_examples()
    assert "session_shell" not in _build_jsonrpc_extension_openapi_examples()
    assert "worktrees_create" not in _build_jsonrpc_extension_openapi_examples()
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
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        lambda _settings, **_kwargs: closable,
    )

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
                "protocol_version": A2A_PROTOCOL_VERSION,
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
                        "metrics_endpoint": {
                            "enabled": True,
                            "availability": "enabled",
                            "path": "/metrics",
                            "authentication": "required",
                            "toggle": "A2A_METRICS_ENABLED",
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
    assert "message_send_session_binding" in root_examples
    assert "session_shell" not in root_examples
    assert "worktrees_create" not in root_examples
    assert "authenticated extended Agent Card" in openapi_first["paths"]["/"]["post"]["description"]


@pytest.mark.asyncio
async def test_rest_adapter_public_stream_route_delegates_to_handler() -> None:
    handler = MagicMock()
    adapter = RestDispatcher(request_handler=handler)

    async def _stream(_request: Request, _context):  # noqa: ANN001
        yield {"id": "evt-1"}

    handler.on_subscribe_to_task = _stream
    response = await adapter.on_subscribe_to_task(
        _request("/tasks/x:subscribe", method="GET", path_params={"id": "x"})
    )
    assert response is not None


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
            "/tasks/task-1/pushNotificationConfigs",
            headers={"Authorization": "Bearer test-token"},
            json={"taskId": "task-1", "url": "https://example.com/hook"},
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": 400,
            "status": "FAILED_PRECONDITION",
            "message": "Push notifications are not supported by the agent",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "PUSH_NOTIFICATION_NOT_SUPPORTED",
                    "domain": "a2a-protocol.org",
                    "metadata": {},
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_metrics_endpoint_is_scrapeable_and_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        DummyChatOpencodeUpstreamClient,
    )
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        anonymous = await client.get("/metrics")
        authenticated = await client.get("/metrics", headers={"Authorization": "Bearer test-token"})

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.headers["content-type"].startswith("text/plain; version=0.0.4")

    disabled_app = create_app(
        make_settings(test_bearer_token="test-token", a2a_metrics_enabled=False)
    )
    disabled_transport = httpx.ASGITransport(app=disabled_app)
    async with httpx.AsyncClient(transport=disabled_transport, base_url="http://test") as client:
        disabled = await client.get("/metrics", headers={"Authorization": "Bearer test-token"})
    assert disabled.status_code == 404


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
            "code": -32003,
            "message": "Push Notification is not supported",
            "data": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "PUSH_NOTIFICATION_NOT_SUPPORTED",
                    "domain": "a2a-protocol.org",
                }
            ],
        },
        "id": 1,
        "jsonrpc": "2.0",
    }


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
            role=Role.ROLE_AGENT,
            parts=[Part(text="server reply")],
        )

    def _stream_failure(params, context=None):  # noqa: ANN001
        del params, context
        return iter_async(terminal_error=InvalidRequestError(message="stream bad"))

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
        send_response = await client.post("/message:send", headers=headers, json=payload)
        stream_response = await client.post("/message:stream", headers=headers, json=payload)

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
                    "metadata": {},
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_task_store_failures_map_to_stable_handler_errors() -> None:
    task_store = MagicMock()
    task_store.get = AsyncMock(side_effect=TaskStoreOperationError("get", "task-1"))
    handler = OpencodeRequestHandler(
        agent_executor=MagicMock(),
        task_store=task_store,
        agent_card=_agent_card(),
    )
    context = ServerCallContext()

    with pytest.raises(InternalError, match="Task store unavailable while loading task state."):
        await handler.on_get_task(GetTaskRequest(id="task-1"), context)
    with pytest.raises(InternalError, match="Task store unavailable while loading task state."):
        async for _event in handler.on_subscribe_to_task(
            SubscribeToTaskRequest(id="task-1"),
            context,
        ):
            pass
    await handler.aclose()


@pytest.mark.asyncio
async def test_output_mode_and_extension_validation_precede_execution() -> None:
    executor = MagicMock()
    executor.execute = AsyncMock()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=MagicMock(),
        agent_card=_agent_card(),
    )
    output_mode_params = types.SimpleNamespace(
        message=types.SimpleNamespace(metadata={}),
        configuration=types.SimpleNamespace(accepted_output_modes=["image/png"]),
    )
    with pytest.raises(UnsupportedOperationError, match="not compatible"):
        await handler.on_message_send(output_mode_params, ServerCallContext())

    extension_params = types.SimpleNamespace(
        message=types.SimpleNamespace(
            metadata={
                "shared": {
                    "session": {"id": "ses-1"},
                    "model": {"providerID": "openai", "modelID": "gpt-5"},
                }
            }
        ),
        configuration=None,
    )
    with pytest.raises(UnsupportedOperationError, match="A2A-Extensions"):
        await handler.on_message_send(extension_params, ServerCallContext())
    executor.execute.assert_not_awaited()
    await handler.aclose()


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

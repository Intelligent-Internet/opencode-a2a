import logging
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.server.routes.rest_dispatcher import RestDispatcher
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
)

from opencode_a2a.a2a_utils import make_data_part
from opencode_a2a.contracts.extensions import (
    MODEL_SELECTION_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
)
from opencode_a2a.output_modes import build_output_negotiation_metadata
from opencode_a2a.server.application import (
    AUTHENTICATED_EXTENDED_CARD_CACHE_CONTROL,
    PUBLIC_AGENT_CARD_CACHE_CONTROL,
    SESSION_MANAGEMENT_EXTENSION_URI,
    _normalize_log_level,
    build_agent_card,
    create_app,
)
from opencode_a2a.trace_context import parse_traceparent
from tests.support.helpers import (
    DummyChatOpencodeUpstreamClient,
    make_basic_auth_header,
    make_settings,
)


def _task_for_listing(
    *,
    task_id: str,
    context_id: str,
    state: TaskState = TaskState.TASK_STATE_COMPLETED,
    timestamp: datetime | str,
    include_artifacts: bool = False,
    history_size: int = 0,
) -> Task:
    status = TaskStatus(state=state)
    if isinstance(timestamp, datetime):
        status.timestamp = timestamp
    else:
        try:
            status.timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            pass
    artifacts: list[Artifact] = []
    if include_artifacts:
        artifacts = [
            Artifact(
                artifact_id=f"{task_id}-artifact",
                parts=[Part(text=f"artifact:{task_id}")],
            )
        ]
    history: list[Message] = []
    if history_size > 0:
        history = [
            Message(
                message_id=f"{task_id}-history-{index}",
                role=Role.ROLE_AGENT,
                parts=[Part(text=f"history:{task_id}:{index}")],
                context_id=context_id,
                task_id=task_id,
            )
            for index in range(history_size)
        ]
    return Task(
        id=task_id,
        context_id=context_id,
        status=status,
        artifacts=artifacts,
        history=history,
    )


def test_agent_card_declares_dual_stack_with_http_json_preferred() -> None:
    card = build_agent_card(make_settings(test_bearer_token="test-token"))

    interfaces = {
        (iface.protocol_binding, iface.protocol_version) for iface in card.supported_interfaces
    }
    assert ("HTTP+JSON", "1.0") in interfaces
    assert ("JSONRPC", "1.0") in interfaces


def test_normalize_log_level_falls_back_to_warning_for_invalid_value() -> None:
    assert _normalize_log_level("warn") == "WARNING"


@pytest.mark.asyncio
async def test_public_agent_card_response_echoes_supplied_traceparent() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/.well-known/agent.json",
            headers={"traceparent": traceparent, "tracestate": "vendor=value"},
        )

    assert response.status_code == 200
    assert response.headers["traceparent"] == traceparent


@pytest.mark.asyncio
async def test_public_agent_card_response_generates_traceparent_when_missing() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/.well-known/agent.json")

    assert response.status_code == 200
    generated = response.headers.get("traceparent")
    assert isinstance(generated, str)
    assert parse_traceparent(generated) is not None


def test_rest_subscription_route_matches_current_sdk_contract() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    route_paths = {route.path for route in app.router.routes if hasattr(route, "path")}

    assert "/v1/tasks/{id}:subscribe" in route_paths
    assert "/v1/tasks/{id}:resubscribe" not in route_paths


def test_rest_subscription_route_registers_distinct_get_and_post_operations() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    subscribe_routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/v1/tasks/{id}:subscribe"
    ]

    assert len(subscribe_routes) == 2
    assert {tuple(sorted(route.methods or set())) for route in subscribe_routes} == {
        ("GET",),
        ("POST",),
    }


def test_rest_dispatcher_exposes_sdk_rest_handlers() -> None:
    rest_dispatcher = RestDispatcher(request_handler=MagicMock())

    assert callable(rest_dispatcher.on_message_send)
    assert callable(rest_dispatcher.on_message_send_stream)
    assert callable(rest_dispatcher.on_get_task)
    assert callable(rest_dispatcher.on_cancel_task)
    assert callable(rest_dispatcher.on_subscribe_to_task)


@pytest.mark.asyncio
async def test_list_tasks_route_returns_paginated_results(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    task_store = app.state.task_store
    now = datetime.now(UTC)
    await task_store.save(
        _task_for_listing(
            task_id="task-new",
            context_id="ctx-list",
            timestamp=(now + timedelta(seconds=2)).isoformat(),
            include_artifacts=True,
            history_size=3,
        )
    )
    await task_store.save(
        _task_for_listing(
            task_id="task-old",
            context_id="ctx-list",
            timestamp=(now + timedelta(seconds=1)).isoformat(),
            include_artifacts=True,
            history_size=2,
        )
    )
    await task_store.save(
        _task_for_listing(
            task_id="task-other",
            context_id="ctx-other",
            state=TaskState.TASK_STATE_WORKING,
            timestamp=now.isoformat(),
            history_size=1,
        )
    )

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_page = await client.get(
            "/v1/tasks",
            headers=headers,
            params={"contextId": "ctx-list", "pageSize": "1"},
        )

        assert first_page.status_code == 200
        first_payload = first_page.json()
        assert first_payload["totalSize"] == 2
        assert first_payload["pageSize"] == 1
        assert first_payload["tasks"][0]["id"] == "task-new"
        assert "artifacts" not in first_payload["tasks"][0]
        assert "history" not in first_payload["tasks"][0]
        assert first_payload["nextPageToken"]

        second_page = await client.get(
            "/v1/tasks",
            headers=headers,
            params={
                "contextId": "ctx-list",
                "pageSize": "1",
                "pageToken": first_payload["nextPageToken"],
            },
        )

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert second_payload["totalSize"] == 2
    assert second_payload["pageSize"] == 1
    assert second_payload["tasks"][0]["id"] == "task-old"
    assert second_payload["nextPageToken"] == ""


@pytest.mark.asyncio
async def test_list_tasks_route_cursor_stays_stable_when_newer_task_is_inserted(
    monkeypatch,
) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    task_store = app.state.task_store
    now = datetime.now(UTC)
    await task_store.save(
        _task_for_listing(
            task_id="task-page-1",
            context_id="ctx-cursor",
            timestamp=(now + timedelta(seconds=3)).isoformat(),
        )
    )
    await task_store.save(
        _task_for_listing(
            task_id="task-page-2",
            context_id="ctx-cursor",
            timestamp=(now + timedelta(seconds=2)).isoformat(),
        )
    )
    await task_store.save(
        _task_for_listing(
            task_id="task-page-3",
            context_id="ctx-cursor",
            timestamp=(now + timedelta(seconds=1)).isoformat(),
        )
    )

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_page = await client.get(
            "/v1/tasks",
            headers=headers,
            params={"contextId": "ctx-cursor", "pageSize": "1"},
        )
        first_payload = first_page.json()
        assert first_payload["tasks"][0]["id"] == "task-page-1"

        await task_store.save(
            _task_for_listing(
                task_id="task-inserted-later",
                context_id="ctx-cursor",
                timestamp=(now + timedelta(seconds=4)).isoformat(),
            )
        )

        second_page = await client.get(
            "/v1/tasks",
            headers=headers,
            params={
                "contextId": "ctx-cursor",
                "pageSize": "1",
                "pageToken": first_payload["nextPageToken"],
            },
        )

    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert second_payload["totalSize"] == 4
    assert second_payload["pageSize"] == 1
    assert second_payload["tasks"][0]["id"] == "task-page-2"


@pytest.mark.asyncio
async def test_list_tasks_route_supports_history_artifacts_and_filters(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    task_store = app.state.task_store
    now = datetime.now(UTC)
    target_task = _task_for_listing(
        task_id="task-filtered",
        context_id="ctx-filtered",
        state=TaskState.TASK_STATE_COMPLETED,
        timestamp=(now + timedelta(seconds=1)).isoformat(),
        include_artifacts=True,
        history_size=4,
    )
    await task_store.save(target_task)
    await task_store.save(
        _task_for_listing(
            task_id="task-excluded-status",
            context_id="ctx-filtered",
            state=TaskState.TASK_STATE_FAILED,
            timestamp=now.isoformat(),
            include_artifacts=True,
            history_size=2,
        )
    )

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/tasks",
            headers=headers,
            params={
                "contextId": "ctx-filtered",
                "status": "completed",
                "historyLength": "2",
                "includeArtifacts": "true",
                "statusTimestampAfter": now.isoformat(),
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalSize"] == 1
    assert payload["pageSize"] == 1
    assert payload["nextPageToken"] == ""
    returned_task = payload["tasks"][0]
    assert returned_task["id"] == "task-filtered"
    assert len(returned_task["history"]) == 2
    assert returned_task["artifacts"][0]["artifactId"] == "task-filtered-artifact"


@pytest.mark.asyncio
async def test_list_tasks_route_applies_persisted_output_negotiation(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    task_store = app.state.task_store
    metadata = build_output_negotiation_metadata(["text/plain"])
    assert metadata is not None

    task = Task(
        id="task-negotiated-list",
        context_id="ctx-negotiated-list",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, timestamp=datetime.now(UTC)),
        artifacts=[
            Artifact(
                artifact_id="task-negotiated-list-text",
                parts=[Part(text="plain")],
            ),
            Artifact(
                artifact_id="task-negotiated-list-json",
                parts=[make_data_part({"tool": "bash", "status": "completed"})],
            ),
        ],
        metadata=metadata,
    )
    await task_store.save(task)

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/tasks",
            headers=headers,
            params={
                "contextId": "ctx-negotiated-list",
                "includeArtifacts": "true",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalSize"] == 1
    assert payload["tasks"][0]["id"] == "task-negotiated-list"
    assert [artifact["artifactId"] for artifact in payload["tasks"][0]["artifacts"]] == [
        "task-negotiated-list-text",
        "task-negotiated-list-json",
    ]
    assert payload["tasks"][0]["artifacts"][1]["parts"][0]["text"] == (
        '{"status":"completed","tool":"bash"}'
    )


@pytest.mark.asyncio
async def test_list_tasks_route_filters_unnegotiated_shared_extension_metadata(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    task_store = app.state.task_store
    metadata = {
        "shared": {
            "session": {"id": "ses-1"},
            "model": {"providerID": "openai", "modelID": "gpt-5"},
            "usage": {"input_tokens": 7},
        },
        "opencode": {"directory": "/workspace"},
    }
    await task_store.save(
        Task(
            id="task-shared-list",
            context_id="ctx-shared-list",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED, timestamp=datetime.now(UTC)),
            metadata=metadata,
        )
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        without_extensions = await client.get(
            "/v1/tasks",
            headers={"Authorization": "Bearer test-token"},
            params={"contextId": "ctx-shared-list"},
        )
        with_extensions = await client.get(
            "/v1/tasks",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Extensions": ",".join(
                    [
                        MODEL_SELECTION_EXTENSION_URI,
                        SESSION_BINDING_EXTENSION_URI,
                        STREAMING_EXTENSION_URI,
                    ]
                ),
            },
            params={"contextId": "ctx-shared-list"},
        )

    assert without_extensions.status_code == 200
    assert without_extensions.json()["tasks"][0]["metadata"] == {
        "opencode": {"directory": "/workspace"}
    }
    assert with_extensions.status_code == 200
    assert with_extensions.json()["tasks"][0]["metadata"]["shared"] == {
        "session": {"id": "ses-1"},
        "model": {"providerID": "openai", "modelID": "gpt-5"},
        "usage": {"input_tokens": 7.0},
    }


@pytest.mark.asyncio
async def test_list_tasks_route_tolerates_invalid_stored_status_timestamp(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    task_store = app.state.task_store
    now = datetime.now(UTC)
    await task_store.save(
        _task_for_listing(
            task_id="task-valid-ts",
            context_id="ctx-invalid-ts",
            timestamp=now.isoformat(),
        )
    )
    await task_store.save(
        _task_for_listing(
            task_id="task-invalid-ts",
            context_id="ctx-invalid-ts",
            timestamp="not-a-timestamp",
        )
    )

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/tasks",
            headers=headers,
            params={"contextId": "ctx-invalid-ts"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalSize"] == 2
    assert [task["id"] for task in payload["tasks"]] == ["task-valid-ts", "task-invalid-ts"]


@pytest.mark.asyncio
async def test_list_tasks_route_validates_query_parameters(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_backend="memory",
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page_size_error = await client.get(
            "/v1/tasks",
            headers=headers,
            params={"pageSize": "0"},
        )
        page_token_error = await client.get(
            "/v1/tasks",
            headers=headers,
            params={"pageToken": "invalid-token"},
        )

    assert page_size_error.status_code == 400
    assert page_size_error.json() == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "pageSize must be between 1 and 100.",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "INVALID_LIST_TASKS_REQUEST",
                    "domain": "a2a-protocol.org",
                    "metadata": {"field": "pageSize"},
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "field": "pageSize",
                },
            ],
        }
    }
    assert page_token_error.status_code == 400
    assert page_token_error.json() == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "pageToken is invalid.",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "INVALID_LIST_TASKS_REQUEST",
                    "domain": "a2a-protocol.org",
                    "metadata": {"field": "pageToken"},
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "field": "pageToken",
                },
            ],
        }
    }


def test_openapi_rest_message_routes_include_schema_and_examples() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    openapi = app.openapi()
    paths = openapi["paths"]

    expected: dict[str, str] = {
        "/v1/message:send": "#/components/schemas/SendMessageRequest",
        "/v1/message:stream": "#/components/schemas/SendStreamingMessageRequest",
    }
    for path, expected_schema_ref in expected.items():
        post = paths[path]["post"]
        assert post["summary"] in {"Send Message (HTTP+JSON)", "Stream Message (HTTP+JSON)"}
        content = post.get("requestBody", {}).get("content", {}).get("application/json", {})
        assert content.get("schema", {}).get("$ref") == expected_schema_ref
        examples = content.get("examples")
        assert isinstance(examples, dict)
        assert "basic_message" in examples
        assert "continue_session" in examples
        assert "message_with_file_input" in examples


def test_openapi_jsonrpc_examples_include_core_message_methods() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    openapi = app.openapi()
    post = openapi["paths"]["/"]["post"]
    examples = (
        post.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("examples", {})
    )
    example_values = examples.values()
    methods = {value.get("value", {}).get("method") for value in example_values}
    assert "SendMessage" in methods
    assert "SendStreamingMessage" in methods
    assert "message_send_file_input" in examples


@pytest.mark.asyncio
async def test_agent_card_routes_split_public_and_authenticated_extended_contracts(
    monkeypatch,
) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        public_card = await client.get("/.well-known/agent-card.json")
        assert public_card.status_code == 200
        assert public_card.headers["cache-control"] == PUBLIC_AGENT_CARD_CACHE_CONTROL
        assert public_card.headers["etag"]
        assert public_card.headers["vary"] == "Accept-Encoding"
        assert public_card.json()["capabilities"]["extendedAgentCard"] is True

        public_cached = await client.get(
            "/.well-known/agent-card.json",
            headers={"If-None-Match": public_card.headers["etag"]},
        )
        assert public_cached.status_code == 304

        unauthorized_extended = await client.get("/agent/authenticatedExtendedCard")
        assert unauthorized_extended.status_code == 401

        extended_card = await client.get("/agent/authenticatedExtendedCard", headers=headers)
        assert extended_card.status_code == 200
        assert extended_card.headers["cache-control"] == AUTHENTICATED_EXTENDED_CARD_CACHE_CONTROL
        assert {
            value.strip() for value in extended_card.headers["vary"].split(",") if value.strip()
        } == {"Authorization", "Accept-Encoding"}
        assert extended_card.headers["etag"]

        extended_cached = await client.get(
            "/agent/authenticatedExtendedCard",
            headers={
                **headers,
                "If-None-Match": extended_card.headers["etag"],
            },
        )
        assert extended_cached.status_code == 304

        public_extensions = {
            item["uri"]: item for item in public_card.json()["capabilities"]["extensions"]
        }
        extended_extensions = {
            item["uri"]: item for item in extended_card.json()["capabilities"]["extensions"]
        }
        assert public_extensions[SESSION_MANAGEMENT_EXTENSION_URI].get("params") is None
        assert extended_extensions[SESSION_MANAGEMENT_EXTENSION_URI]["params"]["methods"][
            "status"
        ] == ("opencode.sessions.status")
        assert len(public_card.content) < len(extended_card.content)

        rpc_card = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": "card-1",
                "method": "GetExtendedAgentCard",
                "params": {},
            },
        )
        assert rpc_card.status_code == 200
        assert (
            rpc_card.json()["result"]["capabilities"]["extensions"][3]["uri"]
            == SESSION_MANAGEMENT_EXTENSION_URI
        )


@pytest.mark.asyncio
async def test_authenticated_extended_card_accepts_basic_auth_when_configured(
    monkeypatch,
) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
        )
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/agent/authenticatedExtendedCard",
            headers=make_basic_auth_header("operator", "op-pass"),
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_rest_endpoints_reject_unsupported_protocol_version() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/message:send",
            headers={
                "Authorization": "Bearer test-token",
                "A2A-Version": "2.0",
            },
            json={
                "message": {
                    "messageId": "req-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                }
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "Unsupported A2A version",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "VERSION_NOT_SUPPORTED",
                    "domain": "a2a-protocol.org",
                    "metadata": {
                        "requestedVersion": "2.0",
                        "supportedProtocolVersions": '["1.0"]',
                        "defaultProtocolVersion": "1.0",
                    },
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "requestedVersion": "2.0",
                    "supportedProtocolVersions": ["1.0"],
                    "defaultProtocolVersion": "1.0",
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_rest_endpoints_return_v1_status_body_for_v1_protocol_errors() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/message:send?A2A-Version=1.1",
            headers={"Authorization": "Bearer test-token"},
            json={
                "message": {
                    "messageId": "req-2",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                }
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "Unsupported A2A version",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "VERSION_NOT_SUPPORTED",
                    "domain": "a2a-protocol.org",
                    "metadata": {
                        "requestedVersion": "1.1",
                        "supportedProtocolVersions": '["1.0"]',
                        "defaultProtocolVersion": "1.0",
                    },
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "requestedVersion": "1.1",
                    "supportedProtocolVersions": ["1.0"],
                    "defaultProtocolVersion": "1.0",
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_global_http_gzip_applies_to_eligible_non_streaming_responses(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        public_response = await client.get(
            "/.well-known/agent-card.json",
            headers={"Accept-Encoding": "gzip"},
        )
        extended_response = await client.get(
            "/agent/authenticatedExtendedCard",
            headers={
                "Authorization": "Bearer test-token",
                "Accept-Encoding": "gzip",
            },
        )
        health_response = await client.get(
            "/health",
            headers={
                "Authorization": "Bearer test-token",
                "Accept-Encoding": "gzip",
            },
        )

    assert extended_response.status_code == 200
    assert extended_response.headers.get("content-encoding") == "gzip"
    assert public_response.status_code == 200
    if len(public_response.content) >= 2048:
        assert public_response.headers.get("content-encoding") == "gzip"
    else:
        assert public_response.headers.get("content-encoding") is None
    assert health_response.status_code == 200
    assert health_response.headers.get("content-encoding") is None


@pytest.mark.asyncio
async def test_http_gzip_minimum_size_setting_can_opt_in_smaller_responses(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_http_gzip_minimum_size=1024,
        )
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        public_response = await client.get(
            "/.well-known/agent-card.json",
            headers={"Accept-Encoding": "gzip"},
        )
        health_response = await client.get(
            "/health",
            headers={
                "Authorization": "Bearer test-token",
                "Accept-Encoding": "gzip",
            },
        )

    assert public_response.status_code == 200
    assert public_response.headers.get("content-encoding") == "gzip"
    assert health_response.status_code == 200
    assert health_response.headers.get("content-encoding") == "gzip"


@pytest.mark.asyncio
async def test_streaming_responses_remain_outside_gzip_middleware(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/message:stream",
            headers={
                "Authorization": "Bearer test-token",
                "Accept-Encoding": "gzip",
            },
            json={
                "message": {
                    "messageId": "gzip-stream-test",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hello"}],
                }
            },
        ) as response:
            assert response.status_code == 200
            assert response.headers.get("content-encoding") is None
            assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_dual_stack_send_accepts_transport_native_payloads(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    rest_payload = {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "parts": [{"text": "hello from rest"}],
        }
    }
    rpc_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m-rpc",
                "role": "ROLE_USER",
                "parts": [{"text": "hello from jsonrpc"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_resp = await client.post("/v1/message:send", headers=headers, json=rest_payload)
        assert rest_resp.status_code == 200

        rpc_resp = await client.post("/", headers=headers, json=rpc_payload)
        assert rpc_resp.status_code == 200
        assert rpc_resp.json().get("error") is None


@pytest.mark.asyncio
async def test_v1_pascalcase_sendmessage_alias_is_accepted(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "A2A-Version": "1.0",
    }
    alias_payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m-rpc-v1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello from v1 dispatch"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rpc_resp = await client.post("/", headers=headers, json=alias_payload)

    assert rpc_resp.status_code == 200
    assert rpc_resp.headers["A2A-Version"] == "1.0"
    assert rpc_resp.json().get("error") is None


@pytest.mark.asyncio
async def test_dual_stack_send_rejects_cross_transport_payload_shapes(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(make_settings(test_bearer_token="test-token"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    rest_with_jsonrpc_shape = {
        "message": {
            "messageId": "m-rest-cross",
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
        }
    }
    full_jsonrpc_envelope = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m-rest-cross-envelope",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello from envelope"}],
            }
        },
    }
    rpc_with_rest_shape = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m-rpc-cross",
                "role": "ROLE_USER",
                "content": [{"text": "hello"}],
            }
        },
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_resp = await client.post(
            "/v1/message:send",
            headers=headers,
            json=rest_with_jsonrpc_shape,
        )
        assert rest_resp.status_code == 400
        assert rest_resp.json() == {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "REST message payload must use ROLE_* values for message.role.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "INVALID_REQUEST",
                        "domain": "a2a-protocol.org",
                    }
                ],
            }
        }

        rest_envelope_resp = await client.post(
            "/v1/message:send",
            headers=headers,
            json=full_jsonrpc_envelope,
        )
        assert rest_envelope_resp.status_code == 400
        assert rest_envelope_resp.json() == {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": (
                    "Invalid JSON-RPC payload for REST endpoint. "
                    "Call POST / for JSON-RPC methods such as SendMessage "
                    "or SendStreamingMessage, or send ProtoJSON "
                    "SendMessageRequest payloads to the REST endpoint."
                ),
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "INVALID_HTTP_JSON_PAYLOAD",
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

        v1_rest_resp = await client.post(
            "/v1/message:send",
            headers={**headers, "A2A-Version": "1.0"},
            json=rest_with_jsonrpc_shape,
        )
        assert v1_rest_resp.status_code == 400
        assert v1_rest_resp.json() == {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "REST message payload must use ROLE_* values for message.role.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "INVALID_REQUEST",
                        "domain": "a2a-protocol.org",
                    },
                ],
            }
        }

        rpc_resp = await client.post("/", headers=headers, json=rpc_with_rest_shape)
        assert rpc_resp.status_code == 200
        payload = rpc_resp.json()
        assert payload["error"]["code"] == -32602


def _rest_message_payload() -> dict:
    return {
        "message": {
            "messageId": "m-rest",
            "role": "ROLE_USER",
            "parts": [{"text": "hello from rest"}],
        }
    }


def _jsonrpc_message_send_payload(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "m-rpc",
                "role": "ROLE_USER",
                "parts": [{"text": text}],
            }
        },
    }


@pytest.mark.asyncio
async def test_log_payloads_keeps_body_for_rest_handler(monkeypatch, caplog) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_log_payloads=True)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.server.application"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/message:send",
                headers=headers,
                json=_rest_message_payload(),
            )

            assert resp.status_code == 200

    assert any(
        "A2A request POST /v1/message:send body=" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_payloads_streaming_response_path(monkeypatch, caplog) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_log_payloads=True)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.server.application"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST", "/v1/message:stream", headers=headers, json=_rest_message_payload()
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        break

    assert any(
        "A2A response /v1/message:stream status=200" in record.message
        or "A2A response /v1/message:stream streaming" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_payloads_omits_non_json_request_body(monkeypatch, caplog) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_log_payloads=True)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/octet-stream",
    }

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.server.application"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/", headers=headers, content=b"\x00\x01\x02\x03")
            assert resp.status_code < 500

    assert any(
        "body=[omitted non-json content-type=application/octet-stream]" in record.message
        for record in caplog.records
    )
    assert "\\x00\\x01\\x02\\x03" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_text_plain_request_body(monkeypatch, caplog) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_log_payloads=True)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "text/plain",
    }
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":'
        '{"messageId":"m","role":"ROLE_USER","parts":[{"text":"secret"}]}}}'
    )

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.server.application"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/", headers=headers, content=body)
            assert resp.status_code < 500

    assert any(
        "body=[omitted non-json content-type=text/plain]" in record.message
        for record in caplog.records
    )
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_when_content_length_missing(monkeypatch, caplog) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_log_payloads=True,
            a2a_log_body_limit=64,
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":'
        b'{"messageId":"m","role":"ROLE_USER","parts":[{"text":"missing-cl"}]}}}'
    )

    async def _body_stream():
        yield body

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.server.application"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/",
                headers=headers,
                content=_body_stream(),
            )
            assert resp.status_code == 200

    assert any(
        "body=[omitted missing content-length with limit=64]" in record.message
        for record in caplog.records
    )
    assert any(
        "body=[omitted request_missing content-length with limit=64]" in record.message
        for record in caplog.records
    )
    assert "missing-cl" not in caplog.text


@pytest.mark.asyncio
async def test_log_payloads_omits_oversized_request_body(monkeypatch, caplog) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_log_payloads=True,
            a2a_log_body_limit=64,
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    oversized_text = "x" * 512

    with caplog.at_level(logging.DEBUG, logger="opencode_a2a.server.application"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/",
                headers=headers,
                json=_jsonrpc_message_send_payload(oversized_text),
            )
            assert resp.status_code == 200

    assert any(
        "body=[omitted content-length=" in record.message and "exceeds limit=64" in record.message
        for record in caplog.records
    )
    assert oversized_text not in caplog.text


@pytest.mark.asyncio
async def test_request_body_limit_rejects_oversized_content_length(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_max_request_body_bytes=64)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            headers=headers,
            json=_jsonrpc_message_send_payload("x" * 512),
        )

    assert resp.status_code == 413
    assert resp.json() == {
        "error": {
            "code": 413,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Request body too large",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "REQUEST_BODY_TOO_LARGE",
                    "domain": "a2a-protocol.org",
                    "metadata": {"maxBytes": "64", "actualSize": str(len(resp.request.content))},
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "maxBytes": 64,
                    "actualSize": len(resp.request.content),
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_request_body_limit_rejects_oversized_stream_without_content_length(
    monkeypatch,
) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_max_request_body_bytes=64)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":'
        b'{"messageId":"m","role":"ROLE_USER","parts":[{"text":"' + (b"x" * 128) + b'"}]}}}'
    )

    async def _body_stream():
        yield body

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/", headers=headers, content=_body_stream())

    assert resp.status_code == 413
    assert resp.json() == {
        "error": {
            "code": 413,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Request body too large",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "REQUEST_BODY_TOO_LARGE",
                    "domain": "a2a-protocol.org",
                    "metadata": {"maxBytes": "64", "actualSize": str(len(body))},
                },
                {
                    "@type": "type.googleapis.com/opencode_a2a.HttpErrorContext",
                    "maxBytes": 64,
                    "actualSize": len(body),
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_request_body_limit_preserves_body_for_downstream_handlers(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(test_bearer_token="test-token", a2a_max_request_body_bytes=4096)
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/",
            headers=headers,
            json=_jsonrpc_message_send_payload("hello after pre-read"),
        )

    assert resp.status_code == 200
    assert resp.json().get("error") is None


def test_create_app_propagates_cancel_abort_timeout(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    captured: dict[str, float | bool | int] = {}

    class _CapturingExecutor:
        def __init__(
            self,
            _client,
            *,
            streaming_enabled: bool,
            cancel_abort_timeout_seconds: float,
            pending_session_claim_ttl_seconds: float,
            a2a_client_manager: object = None,
            session_state_repository: object = None,
        ) -> None:
            captured["streaming_enabled"] = streaming_enabled
            captured["cancel_abort_timeout_seconds"] = cancel_abort_timeout_seconds
            captured["pending_session_claim_ttl_seconds"] = pending_session_claim_ttl_seconds
            captured["a2a_client_manager"] = a2a_client_manager
            captured["session_state_repository"] = session_state_repository

        async def execute(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        async def cancel(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        _sandbox_policy = types.SimpleNamespace(resolve_directory=lambda requested, **_: requested)
        _session_manager = types.SimpleNamespace(
            claim_preferred_session=AsyncMock(return_value=False),
            finalize_session_claim=AsyncMock(),
            release_preferred_session_claim=AsyncMock(),
        )

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    monkeypatch.setattr(app_module, "OpencodeAgentExecutor", _CapturingExecutor)

    app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_cancel_abort_timeout_seconds=0.25,
            a2a_pending_session_claim_ttl_seconds=33.0,
        )
    )

    assert captured["cancel_abort_timeout_seconds"] == 0.25
    assert captured["pending_session_claim_ttl_seconds"] == 33.0


def test_create_app_propagates_outbound_client_settings(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    app = app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_client_timeout_seconds=41.0,
            a2a_client_card_fetch_timeout_seconds=7.0,
            a2a_client_use_client_preference=True,
            a2a_client_bearer_token="peer-token",
            a2a_client_cache_ttl_seconds=321.0,
            a2a_client_cache_maxsize=12,
            a2a_client_supported_transports=("http-json", "json-rpc"),
        )
    )

    client_manager = app.state.a2a_client_manager
    settings = client_manager.client_settings
    assert settings.default_timeout == 41.0
    assert settings.card_fetch_timeout == 7.0
    assert settings.use_client_preference is True
    assert settings.bearer_token == "peer-token"
    assert settings.supported_transports == ("HTTP+JSON", "JSONRPC")
    assert client_manager._cache_ttl_seconds == 321.0  # noqa: SLF001
    assert client_manager._cache_maxsize == 12  # noqa: SLF001


def test_create_app_requires_control_guard_hooks(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    class _BrokenExecutor:
        def __init__(
            self,
            _client,
            *,
            streaming_enabled: bool,
            cancel_abort_timeout_seconds: float,
            pending_session_claim_ttl_seconds: float,
            a2a_client_manager: object = None,
            session_state_repository: object = None,
        ) -> None:
            del (
                streaming_enabled,
                cancel_abort_timeout_seconds,
                pending_session_claim_ttl_seconds,
                a2a_client_manager,
                session_state_repository,
            )
            self._session_manager = types.SimpleNamespace(
                finalize_session_claim=AsyncMock(),
                release_preferred_session_claim=AsyncMock(),
            )
            self._sandbox_policy = types.SimpleNamespace(
                resolve_directory=lambda requested, **_: requested
            )

        async def execute(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

        async def cancel(self, _context, _event_queue) -> None:  # noqa: ANN001
            raise NotImplementedError

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    monkeypatch.setattr(app_module, "OpencodeAgentExecutor", _BrokenExecutor)

    with pytest.raises(ValueError, match="Control methods require guard hooks"):
        app_module.create_app(make_settings(test_bearer_token="test-token"))


def test_create_app_builds_configured_task_store(monkeypatch) -> None:
    import opencode_a2a.server.application as app_module

    captured: dict[str, object] = {}

    def _build_task_store(settings):  # noqa: ANN001
        captured["backend"] = settings.a2a_task_store_backend
        captured["database_url"] = settings.a2a_task_store_database_url
        return MagicMock()

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", DummyChatOpencodeUpstreamClient)
    monkeypatch.setattr(app_module, "build_task_store", _build_task_store)

    app_module.create_app(
        make_settings(
            test_bearer_token="test-token",
            a2a_task_store_database_url="sqlite+aiosqlite:///./test.db",
        )
    )

    assert captured == {
        "backend": "database",
        "database_url": "sqlite+aiosqlite:///./test.db",
    }

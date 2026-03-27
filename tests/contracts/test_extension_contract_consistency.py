import httpx
import pytest

from opencode_a2a.contracts.extensions import (
    INTERRUPT_CALLBACK_METHODS,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    build_capability_snapshot,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_interrupt_recovery_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_subtask_capability_extension_params,
    build_wire_contract_params,
)
from opencode_a2a.jsonrpc.application import SESSION_CONTEXT_PREFIX
from opencode_a2a.profile.runtime import build_runtime_profile
from opencode_a2a.server.application import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    SUBTASK_CAPABILITY_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    build_agent_card,
    create_app,
)
from tests.support.helpers import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.helpers import make_settings


def test_extension_ssot_matches_agent_card_contracts() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_binding = ext_by_uri[SESSION_BINDING_EXTENSION_URI]
    model_selection = ext_by_uri[MODEL_SELECTION_EXTENSION_URI]
    streaming = ext_by_uri[STREAMING_EXTENSION_URI]
    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    subtask_capability = ext_by_uri[SUBTASK_CAPABILITY_EXTENSION_URI]
    provider_discovery = ext_by_uri[PROVIDER_DISCOVERY_EXTENSION_URI]
    interrupt_recovery = ext_by_uri[INTERRUPT_RECOVERY_EXTENSION_URI]
    interrupt_callback = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    compatibility_profile = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    settings = make_settings(a2a_bearer_token="test-token")
    runtime_profile = build_runtime_profile(settings)
    expected_session_binding = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_model_selection = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_streaming = build_streaming_extension_params()
    expected_session_query = build_session_query_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_subtask_capability = build_subtask_capability_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_provider_discovery = build_provider_discovery_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_interrupt_recovery = build_interrupt_recovery_extension_params(
        runtime_profile=runtime_profile,
    )
    assert expected_session_query["pagination"]["default_limit"] == SESSION_QUERY_DEFAULT_LIMIT
    assert expected_session_query["pagination"]["max_limit"] == SESSION_QUERY_MAX_LIMIT
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    expected_wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )

    assert session_binding.params == expected_session_binding, (
        "Session binding extension drifted from contracts.extensions SSOT."
    )
    assert model_selection.params == expected_model_selection, (
        "Model selection extension drifted from contracts.extensions SSOT."
    )
    assert streaming.params == expected_streaming, (
        "Streaming extension drifted from contracts.extensions SSOT."
    )
    assert session_query.params == expected_session_query, (
        "Session query extension drifted from contracts.extensions SSOT."
    )
    assert subtask_capability.params == expected_subtask_capability, (
        "Subtask capability extension drifted from contracts.extensions SSOT."
    )
    assert provider_discovery.params == expected_provider_discovery, (
        "Provider discovery extension drifted from contracts.extensions SSOT."
    )
    assert interrupt_recovery.params == expected_interrupt_recovery, (
        "Interrupt recovery extension drifted from contracts.extensions SSOT."
    )
    assert interrupt_callback.params == expected_interrupt_callback, (
        "Interrupt callback extension drifted from contracts.extensions SSOT."
    )
    assert compatibility_profile.params == expected_compatibility_profile, (
        "Compatibility profile extension drifted from contracts.extensions SSOT."
    )
    assert wire_contract.params == expected_wire_contract, (
        "Wire contract extension drifted from contracts.extensions SSOT."
    )


def test_openapi_jsonrpc_contract_extension_matches_ssot() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    openapi = app.openapi()
    post = openapi["paths"]["/"]["post"]

    contract = post.get("x-a2a-extension-contracts")
    assert isinstance(contract, dict), (
        "POST / OpenAPI is missing x-a2a-extension-contracts metadata."
    )

    session_binding = contract["session_binding"]
    model_selection = contract["model_selection"]
    streaming = contract["streaming"]
    session_query = contract["session_query"]
    subtask_capability = contract["subtask_capability"]
    provider_discovery = contract["provider_discovery"]
    interrupt_recovery = contract["interrupt_recovery"]
    interrupt_callback = contract["interrupt_callback"]
    compatibility_profile = contract["compatibility_profile"]
    wire_contract = contract["wire_contract"]
    settings = make_settings(a2a_bearer_token="test-token")
    runtime_profile = build_runtime_profile(settings)
    expected_session_binding = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_model_selection = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_streaming = build_streaming_extension_params()
    expected_session_query = build_session_query_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_subtask_capability = build_subtask_capability_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_provider_discovery = build_provider_discovery_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_interrupt_recovery = build_interrupt_recovery_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    expected_wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )

    assert session_binding == expected_session_binding, (
        "OpenAPI session binding contract drifted from contracts.extensions SSOT."
    )
    assert model_selection == expected_model_selection, (
        "OpenAPI model selection contract drifted from contracts.extensions SSOT."
    )
    assert streaming == expected_streaming, (
        "OpenAPI streaming contract drifted from contracts.extensions SSOT."
    )
    assert session_query == expected_session_query, (
        "OpenAPI session query contract drifted from contracts.extensions SSOT."
    )
    assert subtask_capability == expected_subtask_capability, (
        "OpenAPI subtask capability contract drifted from contracts.extensions SSOT."
    )
    assert provider_discovery == expected_provider_discovery, (
        "OpenAPI provider discovery contract drifted from contracts.extensions SSOT."
    )
    assert interrupt_recovery == expected_interrupt_recovery, (
        "OpenAPI interrupt recovery contract drifted from contracts.extensions SSOT."
    )
    assert interrupt_callback == expected_interrupt_callback, (
        "OpenAPI interrupt callback contract drifted from contracts.extensions SSOT."
    )
    assert compatibility_profile == expected_compatibility_profile, (
        "OpenAPI compatibility profile contract drifted from contracts.extensions SSOT."
    )
    assert wire_contract == expected_wire_contract, (
        "OpenAPI wire contract drifted from contracts.extensions SSOT."
    )

    json_request_schema = (
        post.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    )
    assert json_request_schema.get("$ref") == "#/components/schemas/A2ARequest", (
        "POST / OpenAPI requestBody schema regressed."
    )

    example_values = (
        post.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("examples", {})
        .values()
    )
    example_methods = {
        value.get("value", {}).get("method") for value in example_values if isinstance(value, dict)
    }
    expected_methods = set(session_query["methods"].values()) | set(
        INTERRUPT_CALLBACK_METHODS.values()
    )
    expected_methods |= {
        "opencode.providers.list",
        "opencode.models.list",
        "opencode.permissions.list",
        "opencode.questions.list",
    }
    missing_methods = sorted(method for method in expected_methods if method not in example_methods)
    assert not missing_methods, (
        "OpenAPI JSON-RPC examples are missing extension methods: " + ", ".join(missing_methods)
    )


def test_openapi_jsonrpc_examples_use_declared_default_session_limit() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    examples = app.openapi()["paths"]["/"]["post"]["requestBody"]["content"]["application/json"][
        "examples"
    ]

    assert examples["session_list"]["value"]["params"]["limit"] == SESSION_QUERY_DEFAULT_LIMIT
    assert examples["session_messages"]["value"]["params"]["limit"] == SESSION_QUERY_DEFAULT_LIMIT


@pytest.mark.asyncio
@pytest.mark.parametrize("session_shell_enabled", [False, True])
async def test_runtime_supported_methods_align_with_capability_snapshot(
    session_shell_enabled: bool,
) -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_enable_session_shell=session_shell_enabled,
    )
    app = create_app(settings)
    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer test-token"},
            json={"jsonrpc": "2.0", "id": 901, "method": "unsupported.method", "params": {}},
        )

    assert response.status_code == 200
    error = response.json()["error"]
    assert error["data"]["supported_methods"] == capability_snapshot.supported_jsonrpc_methods()
    assert error["data"]["supported_methods"] == wire_contract["all_jsonrpc_methods"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "interrupt_type"),
    [
        ("opencode.sessions.list", {}, None),
        ("opencode.sessions.messages.list", {"session_id": "s-1"}, None),
        (
            "opencode.sessions.prompt_async",
            {
                "session_id": "s-1",
                "request": {"parts": [{"type": "text", "text": "Continue"}]},
            },
            None,
        ),
        (
            "opencode.sessions.command",
            {
                "session_id": "s-1",
                "request": {"command": "/review", "arguments": "security"},
            },
            None,
        ),
        (
            "opencode.sessions.shell",
            {
                "session_id": "s-1",
                "request": {"agent": "code-reviewer", "command": "git status --short"},
            },
            None,
        ),
        ("opencode.providers.list", {}, None),
        ("opencode.models.list", {"provider_id": "openai"}, None),
        ("opencode.permissions.list", {}, None),
        ("opencode.questions.list", {}, None),
        (
            "a2a.interrupt.permission.reply",
            {"request_id": "req-perm", "reply": "once"},
            "permission",
        ),
        (
            "a2a.interrupt.question.reply",
            {"request_id": "req-question-reply", "answers": [["ok"]]},
            "question",
        ),
        ("a2a.interrupt.question.reject", {"request_id": "req-question-reject"}, "question"),
    ],
)
async def test_extension_notification_contracts_return_204(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    params: dict[str, object],
    interrupt_type: str | None,
) -> None:
    import opencode_a2a.server.application as app_module

    dummy = DummyOpencodeUpstreamClient(
        make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False)
    )
    if interrupt_type is not None:
        request_id = params["request_id"]
        assert isinstance(request_id, str)
        await dummy.remember_interrupt_request(
            request_id=request_id,
            session_id="s-1",
            interrupt_type=interrupt_type,
        )

    monkeypatch.setattr(app_module, "OpencodeUpstreamClient", lambda _settings: dummy)
    app = app_module.create_app(make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={"jsonrpc": "2.0", "method": method, "params": params},
        )
    assert response.status_code == 204

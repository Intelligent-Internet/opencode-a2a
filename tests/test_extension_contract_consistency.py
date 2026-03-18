import httpx
import pytest

from opencode_a2a_server.app import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    build_agent_card,
    create_app,
)
from opencode_a2a_server.extension_contracts import (
    INTERRUPT_CALLBACK_METHODS,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_params,
)
from opencode_a2a_server.jsonrpc_ext import SESSION_CONTEXT_PREFIX
from tests.helpers import DummySessionQueryOpencodeClient as DummyOpencodeClient
from tests.helpers import make_settings


def test_extension_ssot_matches_agent_card_contracts() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_binding = ext_by_uri[SESSION_BINDING_EXTENSION_URI]
    model_selection = ext_by_uri[MODEL_SELECTION_EXTENSION_URI]
    streaming = ext_by_uri[STREAMING_EXTENSION_URI]
    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    provider_discovery = ext_by_uri[PROVIDER_DISCOVERY_EXTENSION_URI]
    interrupt_callback = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    compatibility_profile = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    deployment_context = session_query.params["deployment_context"]

    settings = make_settings(a2a_bearer_token="test-token")
    expected_session_binding = build_session_binding_extension_params(
        deployment_context=deployment_context,
        directory_override_enabled=True,
    )
    expected_model_selection = build_model_selection_extension_params(
        deployment_context=deployment_context,
    )
    expected_streaming = build_streaming_extension_params()
    expected_session_query = build_session_query_extension_params(
        deployment_context=deployment_context,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_provider_discovery = build_provider_discovery_extension_params(
        deployment_context=deployment_context,
    )
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )
    expected_compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )
    expected_wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )

    assert session_binding.params == expected_session_binding, (
        "Session binding extension drifted from extension_contracts SSOT."
    )
    assert model_selection.params == expected_model_selection, (
        "Model selection extension drifted from extension_contracts SSOT."
    )
    assert streaming.params == expected_streaming, (
        "Streaming extension drifted from extension_contracts SSOT."
    )
    assert session_query.params == expected_session_query, (
        "Session query extension drifted from extension_contracts SSOT."
    )
    assert provider_discovery.params == expected_provider_discovery, (
        "Provider discovery extension drifted from extension_contracts SSOT."
    )
    assert interrupt_callback.params == expected_interrupt_callback, (
        "Interrupt callback extension drifted from extension_contracts SSOT."
    )
    assert compatibility_profile.params == expected_compatibility_profile, (
        "Compatibility profile extension drifted from extension_contracts SSOT."
    )
    assert wire_contract.params == expected_wire_contract, (
        "Wire contract extension drifted from extension_contracts SSOT."
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
    provider_discovery = contract["provider_discovery"]
    interrupt_callback = contract["interrupt_callback"]
    compatibility_profile = contract["compatibility_profile"]
    wire_contract = contract["wire_contract"]
    deployment_context = session_query["deployment_context"]
    settings = make_settings(a2a_bearer_token="test-token")
    expected_session_binding = build_session_binding_extension_params(
        deployment_context=deployment_context,
        directory_override_enabled=True,
    )
    expected_model_selection = build_model_selection_extension_params(
        deployment_context=deployment_context,
    )
    expected_streaming = build_streaming_extension_params()
    expected_session_query = build_session_query_extension_params(
        deployment_context=deployment_context,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_provider_discovery = build_provider_discovery_extension_params(
        deployment_context=deployment_context,
    )
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )
    expected_compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )
    expected_wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )

    assert session_binding == expected_session_binding, (
        "OpenAPI session binding contract drifted from extension_contracts SSOT."
    )
    assert model_selection == expected_model_selection, (
        "OpenAPI model selection contract drifted from extension_contracts SSOT."
    )
    assert streaming == expected_streaming, (
        "OpenAPI streaming contract drifted from extension_contracts SSOT."
    )
    assert session_query == expected_session_query, (
        "OpenAPI session query contract drifted from extension_contracts SSOT."
    )
    assert provider_discovery == expected_provider_discovery, (
        "OpenAPI provider discovery contract drifted from extension_contracts SSOT."
    )
    assert interrupt_callback == expected_interrupt_callback, (
        "OpenAPI interrupt callback contract drifted from extension_contracts SSOT."
    )
    assert compatibility_profile == expected_compatibility_profile, (
        "OpenAPI compatibility profile contract drifted from extension_contracts SSOT."
    )
    assert wire_contract == expected_wire_contract, (
        "OpenAPI wire contract drifted from extension_contracts SSOT."
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
    expected_methods = (
        set(session_query["methods"].values())
        | set(provider_discovery["methods"].values())
        | set(INTERRUPT_CALLBACK_METHODS.values())
    )
    missing_methods = sorted(method for method in expected_methods if method not in example_methods)
    assert not missing_methods, (
        "OpenAPI JSON-RPC examples are missing extension methods: " + ", ".join(missing_methods)
    )


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
    import opencode_a2a_server.app as app_module

    dummy = DummyOpencodeClient(make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False))
    if interrupt_type is not None:
        request_id = params["request_id"]
        assert isinstance(request_id, str)
        dummy.remember_interrupt_request(
            request_id=request_id,
            session_id="s-1",
            interrupt_type=interrupt_type,
        )

    monkeypatch.setattr(app_module, "OpencodeClient", lambda _settings: dummy)
    app = app_module.create_app(make_settings(a2a_bearer_token="t-1", a2a_log_payloads=False))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers={"Authorization": "Bearer t-1"},
            json={"jsonrpc": "2.0", "method": method, "params": params},
        )
    assert response.status_code == 204

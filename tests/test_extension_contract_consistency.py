import httpx
import pytest

from opencode_a2a_serve.app import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    build_agent_card,
    create_app,
)
from opencode_a2a_serve.extension_contracts import (
    INTERRUPT_CALLBACK_METHODS,
    SESSION_QUERY_METHODS,
    build_interrupt_callback_extension_params,
    build_session_query_extension_params,
)
from opencode_a2a_serve.jsonrpc_ext import SESSION_CONTEXT_PREFIX
from tests.helpers import DummySessionQueryOpencodeClient as DummyOpencodeClient
from tests.helpers import make_settings


def test_extension_ssot_matches_agent_card_contracts() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    interrupt_callback = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    deployment_context = session_query.params["deployment_context"]

    expected_session_query = build_session_query_extension_params(
        deployment_context=deployment_context,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )

    assert session_query.params == expected_session_query, (
        "Session query extension drifted from extension_contracts SSOT."
    )
    assert interrupt_callback.params == expected_interrupt_callback, (
        "Interrupt callback extension drifted from extension_contracts SSOT."
    )


def test_openapi_jsonrpc_contract_extension_matches_ssot() -> None:
    app = create_app(make_settings(a2a_bearer_token="test-token"))
    openapi = app.openapi()
    post = openapi["paths"]["/"]["post"]

    contract = post.get("x-opencode-extension-contracts")
    assert isinstance(contract, dict), (
        "POST / OpenAPI is missing x-opencode-extension-contracts metadata."
    )

    session_query = contract["session_query"]
    interrupt_callback = contract["interrupt_callback"]
    deployment_context = session_query["deployment_context"]
    expected_session_query = build_session_query_extension_params(
        deployment_context=deployment_context,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )

    assert session_query == expected_session_query, (
        "OpenAPI session query contract drifted from extension_contracts SSOT."
    )
    assert interrupt_callback == expected_interrupt_callback, (
        "OpenAPI interrupt callback contract drifted from extension_contracts SSOT."
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
    expected_methods = set(SESSION_QUERY_METHODS.values()) | set(
        INTERRUPT_CALLBACK_METHODS.values()
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
        ("opencode.permission.reply", {"request_id": "req-perm", "reply": "once"}, "permission"),
        (
            "opencode.question.reply",
            {"request_id": "req-question-reply", "answers": [["ok"]]},
            "question",
        ),
        ("opencode.question.reject", {"request_id": "req-question-reject"}, "question"),
    ],
)
async def test_extension_notification_contracts_return_204(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    params: dict[str, object],
    interrupt_type: str | None,
) -> None:
    import opencode_a2a_serve.app as app_module

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

from opencode_a2a_serve.app import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    build_agent_card,
)
from opencode_a2a_serve.jsonrpc_ext import SESSION_CONTEXT_PREFIX
from tests.helpers import make_settings


def test_agent_card_description_reflects_actual_transport_capabilities() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    assert "HTTP+JSON and JSON-RPC transports" in card.description
    assert "message/send, message/stream" in card.description
    assert "tasks/get, tasks/cancel" in card.description
    assert (
        "all consumers share the same underlying OpenCode workspace/environment" in card.description
    )


def test_agent_card_injects_deployment_context_into_extensions() -> None:
    card = build_agent_card(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_project="alpha",
            opencode_directory="/srv/workspaces/alpha",
            opencode_provider_id="google",
            opencode_model_id="gemini-2.5-flash",
            opencode_agent="code-reviewer",
            opencode_variant="safe",
            a2a_allow_directory_override=False,
        )
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    binding = ext_by_uri[SESSION_BINDING_EXTENSION_URI]
    context = binding.params["deployment_context"]
    assert context["project"] == "alpha"
    assert context["workspace_root"] == "/srv/workspaces/alpha"
    assert context["provider_id"] == "google"
    assert context["model_id"] == "gemini-2.5-flash"
    assert context["agent"] == "code-reviewer"
    assert context["variant"] == "safe"
    assert context["allow_directory_override"] is False
    assert context["shared_workspace_across_consumers"] is True
    assert binding.params["metadata_namespace"] == "opencode"
    assert binding.params["metadata_key"] == "opencode.session_id"
    assert binding.params["supported_metadata"] == [
        "opencode.session_id",
        "opencode.directory",
    ]
    assert binding.params["directory_override_enabled"] is False
    assert binding.params["shared_workspace_across_consumers"] is True
    assert binding.params["tenant_isolation"] == "none"

    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    assert session_query.params["deployment_context"]["project"] == "alpha"
    assert session_query.params["shared_workspace_across_consumers"] is True
    assert session_query.params["tenant_isolation"] == "none"
    assert session_query.params["methods"]["prompt_async"] == "opencode.sessions.prompt_async"
    assert session_query.params["pagination"]["applies_to"] == [
        "opencode.sessions.list",
        "opencode.sessions.messages.list",
    ]
    prompt_contract = session_query.params["method_contracts"]["opencode.sessions.prompt_async"]
    list_contract = session_query.params["method_contracts"]["opencode.sessions.list"]
    messages_contract = session_query.params["method_contracts"]["opencode.sessions.messages.list"]
    assert prompt_contract["params"]["required"] == ["session_id", "request.parts"]
    assert prompt_contract["result"]["fields"] == ["ok", "session_id"]
    assert list_contract["notification_response_status"] == 204
    assert messages_contract["notification_response_status"] == 204
    assert prompt_contract["notification_response_status"] == 204
    result_envelope = session_query.params["result_envelope"]["by_method"]
    assert result_envelope["opencode.sessions.list"]["fields"] == ["items"]
    assert result_envelope["opencode.sessions.messages.list"]["fields"] == ["items"]
    assert result_envelope["opencode.sessions.prompt_async"]["fields"] == ["ok", "session_id"]
    assert (
        session_query.params["context_semantics"]["a2a_context_id_prefix"] == SESSION_CONTEXT_PREFIX
    )
    assert (
        session_query.params["context_semantics"]["upstream_session_id_field"]
        == "metadata.opencode.session_id"
    )
    assert session_query.params["errors"]["invalid_params_data_fields"] == [
        "type",
        "field",
        "fields",
        "supported",
        "unsupported",
    ]

    interrupt = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    assert interrupt.params["deployment_context"]["project"] == "alpha"
    assert interrupt.params["shared_workspace_across_consumers"] is True
    assert interrupt.params["tenant_isolation"] == "none"
    assert interrupt.params["metadata_namespace"] == "opencode"
    assert interrupt.params["supported_metadata"] == ["opencode.directory"]
    assert interrupt.params["context_fields"]["directory"] == "metadata.opencode.directory"
    assert interrupt.params["errors"]["business_codes"] == {
        "INTERRUPT_REQUEST_NOT_FOUND": -32004,
        "UPSTREAM_UNREACHABLE": -32002,
        "UPSTREAM_HTTP_ERROR": -32003,
    }
    assert interrupt.params["errors"]["error_types"] == [
        "INTERRUPT_REQUEST_NOT_FOUND",
        "INTERRUPT_REQUEST_EXPIRED",
        "INTERRUPT_TYPE_MISMATCH",
        "UPSTREAM_UNREACHABLE",
        "UPSTREAM_HTTP_ERROR",
    ]
    assert interrupt.params["errors"]["invalid_params_data_fields"] == [
        "type",
        "field",
        "fields",
        "request_id",
        "expected",
        "actual",
    ]
    for method_name in (
        "opencode.permission.reply",
        "opencode.question.reply",
        "opencode.question.reject",
    ):
        assert (
            interrupt.params["method_contracts"][method_name]["notification_response_status"] == 204
        )


def test_agent_card_chat_examples_include_project_hint_when_configured() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token", a2a_project="alpha"))
    chat_skill = next(skill for skill in card.skills if skill.id == "opencode.chat")
    assert any("project alpha" in example for example in chat_skill.examples)

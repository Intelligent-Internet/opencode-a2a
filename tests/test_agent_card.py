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
)
from opencode_a2a_server.extension_contracts import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
)
from opencode_a2a_server.jsonrpc_ext import SESSION_CONTEXT_PREFIX
from tests.helpers import make_settings


def test_agent_card_description_reflects_actual_transport_capabilities() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    assert "HTTP+JSON and JSON-RPC transports" in card.description
    assert "message/send, message/stream" in card.description
    assert "tasks/get, tasks/cancel" in card.description
    assert (
        "all consumers share the same underlying OpenCode workspace/environment" in card.description
    )
    assert "single-tenant, self-hosted coding workflows" in card.description
    assert card.capabilities.streaming is True
    assert card.default_input_modes == ["text/plain", "application/octet-stream"]
    assert list(card.security_schemes.keys()) == ["bearerAuth"]
    assert card.security == [{"bearerAuth": []}]


def test_agent_card_injects_deployment_context_into_extensions() -> None:
    card = build_agent_card(
        make_settings(
            a2a_bearer_token="test-token",
            a2a_project="alpha",
            opencode_workspace_root="/srv/workspaces/alpha",
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
    assert context["agent"] == "code-reviewer"
    assert context["variant"] == "safe"
    assert context["deployment_profile"] == "single_tenant_shared_workspace"
    assert context["allow_directory_override"] is False
    assert context["shared_workspace_across_consumers"] is True
    assert context["tenant_isolation"] == "none"
    assert binding.params["metadata_field"] == "metadata.shared.session.id"
    assert binding.params["supported_metadata"] == [
        "shared.session.id",
        "opencode.directory",
    ]
    assert binding.params["provider_private_metadata"] == ["opencode.directory"]
    assert binding.params["directory_override_enabled"] is False
    assert binding.params["shared_workspace_across_consumers"] is True
    assert binding.params["tenant_isolation"] == "none"

    model_selection = ext_by_uri[MODEL_SELECTION_EXTENSION_URI]
    assert model_selection.params["metadata_field"] == "metadata.shared.model"
    assert model_selection.params["fields"]["providerID"] == "metadata.shared.model.providerID"
    assert model_selection.params["fields"]["modelID"] == "metadata.shared.model.modelID"
    assert model_selection.params["applies_to_methods"] == ["message/send", "message/stream"]
    assert model_selection.params["behavior"] == "prefer_metadata_model_else_upstream_default"

    streaming = ext_by_uri[STREAMING_EXTENSION_URI]
    assert streaming.params["artifact_metadata_field"] == "metadata.shared.stream"
    assert streaming.params["progress_metadata_field"] == "metadata.shared.progress"
    assert streaming.params["interrupt_metadata_field"] == "metadata.shared.interrupt"
    assert streaming.params["usage_metadata_field"] == "metadata.shared.usage"
    assert streaming.params["stream_fields"]["sequence"] == "metadata.shared.stream.sequence"
    assert streaming.params["progress_fields"]["type"] == "metadata.shared.progress.type"

    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    assert session_query.params["deployment_context"]["project"] == "alpha"
    assert session_query.params["shared_workspace_across_consumers"] is True
    assert session_query.params["tenant_isolation"] == "none"
    assert session_query.params["control_methods"] == {
        "prompt_async": "opencode.sessions.prompt_async",
        "command": "opencode.sessions.command",
    }
    assert session_query.params["methods"]["prompt_async"] == "opencode.sessions.prompt_async"
    assert session_query.params["methods"]["command"] == "opencode.sessions.command"
    assert "shell" not in session_query.params["methods"]
    assert session_query.params["control_method_flags"]["opencode.sessions.shell"] == {
        "enabled_by_default": False,
        "config_key": "A2A_ENABLE_SESSION_SHELL",
    }
    assert session_query.params["pagination"]["default_limit"] == SESSION_QUERY_DEFAULT_LIMIT
    assert session_query.params["pagination"]["max_limit"] == SESSION_QUERY_MAX_LIMIT
    assert session_query.params["pagination"]["applies_to"] == [
        "opencode.sessions.list",
        "opencode.sessions.messages.list",
    ]
    prompt_contract = session_query.params["method_contracts"]["opencode.sessions.prompt_async"]
    command_contract = session_query.params["method_contracts"]["opencode.sessions.command"]
    list_contract = session_query.params["method_contracts"]["opencode.sessions.list"]
    messages_contract = session_query.params["method_contracts"]["opencode.sessions.messages.list"]
    assert prompt_contract["params"]["required"] == ["session_id", "request.parts"]
    assert prompt_contract["result"]["fields"] == ["ok", "session_id"]
    assert command_contract["params"]["required"] == [
        "session_id",
        "request.command",
        "request.arguments",
    ]
    assert command_contract["result"]["fields"] == ["item"]
    assert list_contract["notification_response_status"] == 204
    assert messages_contract["notification_response_status"] == 204
    assert prompt_contract["notification_response_status"] == 204
    assert "result_envelope" not in session_query.params
    assert "opencode.sessions.shell" not in session_query.params["method_contracts"]
    assert (
        session_query.params["context_semantics"]["a2a_context_id_prefix"] == SESSION_CONTEXT_PREFIX
    )
    assert (
        session_query.params["context_semantics"]["upstream_session_id_field"]
        == "metadata.shared.session.id"
    )
    assert session_query.params["errors"]["business_codes"] == {
        "SESSION_NOT_FOUND": -32001,
        "SESSION_FORBIDDEN": -32006,
        "UPSTREAM_UNREACHABLE": -32002,
        "UPSTREAM_HTTP_ERROR": -32003,
        "UPSTREAM_PAYLOAD_ERROR": -32005,
    }
    assert session_query.params["errors"]["error_data_fields"] == [
        "type",
        "method",
        "session_id",
        "upstream_status",
        "detail",
    ]
    assert session_query.params["errors"]["invalid_params_data_fields"] == [
        "type",
        "field",
        "fields",
        "supported",
        "unsupported",
    ]

    provider_discovery = ext_by_uri[PROVIDER_DISCOVERY_EXTENSION_URI]
    assert provider_discovery.params["deployment_context"]["project"] == "alpha"
    assert provider_discovery.params["methods"] == {
        "list_providers": "opencode.providers.list",
        "list_models": "opencode.models.list",
    }
    assert "result_envelope" not in provider_discovery.params
    assert provider_discovery.params["method_contracts"]["opencode.providers.list"]["result"] == {
        "fields": ["items", "default_by_provider", "connected"],
        "items_type": "ProviderSummary[]",
    }
    assert provider_discovery.params["method_contracts"]["opencode.models.list"]["params"] == {
        "optional": ["provider_id"]
    }
    assert provider_discovery.params["method_contracts"]["opencode.models.list"]["result"] == {
        "fields": ["items", "default_by_provider", "connected"],
        "items_type": "ModelSummary[]",
    }
    assert provider_discovery.params["errors"]["business_codes"] == {
        "UPSTREAM_UNREACHABLE": -32002,
        "UPSTREAM_HTTP_ERROR": -32003,
        "UPSTREAM_PAYLOAD_ERROR": -32005,
    }

    interrupt = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    assert interrupt.params["deployment_context"]["project"] == "alpha"
    assert interrupt.params["shared_workspace_across_consumers"] is True
    assert interrupt.params["tenant_isolation"] == "none"
    assert interrupt.params["request_id_field"] == "metadata.shared.interrupt.request_id"
    assert interrupt.params["supported_metadata"] == ["opencode.directory"]
    assert interrupt.params["provider_private_metadata"] == ["opencode.directory"]
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
        "a2a.interrupt.permission.reply",
        "a2a.interrupt.question.reply",
        "a2a.interrupt.question.reject",
    ):
        assert (
            interrupt.params["method_contracts"][method_name]["notification_response_status"] == 204
        )

    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    assert compatibility.params["extension_retention"][MODEL_SELECTION_EXTENSION_URI] == {
        "surface": "core-runtime-metadata",
        "availability": "always",
        "retention": "stable",
    }
    assert compatibility.params["extension_retention"][PROVIDER_DISCOVERY_EXTENSION_URI] == {
        "surface": "jsonrpc-extension",
        "availability": "always",
        "retention": "stable",
    }
    shell_policy = compatibility.params["method_retention"]["opencode.sessions.shell"]
    assert shell_policy["availability"] == "disabled"
    assert shell_policy["retention"] == "deployment-conditional"
    assert shell_policy["toggle"] == "A2A_ENABLE_SESSION_SHELL"

    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert MODEL_SELECTION_EXTENSION_URI in wire_contract.params["extensions"]["extension_uris"]
    assert PROVIDER_DISCOVERY_EXTENSION_URI in wire_contract.params["extensions"]["extension_uris"]
    assert "opencode.sessions.shell" not in wire_contract.params["all_jsonrpc_methods"]
    assert wire_contract.params["extensions"]["conditionally_available_methods"] == {
        "opencode.sessions.shell": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_SESSION_SHELL",
        }
    }


def test_agent_card_chat_examples_include_project_hint_when_configured() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token", a2a_project="alpha"))
    chat_skill = next(skill for skill in card.skills if skill.id == "opencode.chat")
    assert any("project alpha" in example for example in chat_skill.examples)
    assert any("attached diff" in example for example in chat_skill.examples)
    assert "TextPart and FilePart" in chat_skill.description
    assert "core-a2a" in chat_skill.tags
    assert "portable" in chat_skill.tags


def test_agent_card_contracts_include_shell_when_enabled() -> None:
    card = build_agent_card(
        make_settings(a2a_bearer_token="test-token", a2a_enable_session_shell=True)
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    assert session_query.params["control_methods"]["shell"] == "opencode.sessions.shell"
    assert session_query.params["methods"]["shell"] == "opencode.sessions.shell"
    assert "opencode.sessions.shell" in session_query.params["method_contracts"]

    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    shell_policy = compatibility.params["method_retention"]["opencode.sessions.shell"]
    assert shell_policy["availability"] == "enabled"

    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert "opencode.sessions.shell" in wire_contract.params["all_jsonrpc_methods"]
    assert wire_contract.params["extensions"]["conditionally_available_methods"] == {}

    session_skill = next(skill for skill in card.skills if skill.id == "opencode.sessions.query")
    assert any("opencode.sessions.shell" in example for example in session_skill.examples)


def test_agent_card_skills_hide_shell_when_disabled_by_default() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token"))

    session_skill = next(skill for skill in card.skills if skill.id == "opencode.sessions.query")
    provider_skill = next(skill for skill in card.skills if skill.id == "opencode.providers.query")

    assert "provider-private" in session_skill.tags
    assert "provider-private" in session_skill.description
    assert all("opencode.sessions.shell" not in example for example in session_skill.examples)
    assert "provider-private" in provider_skill.tags
    assert any("opencode.providers.list" in example for example in provider_skill.examples)

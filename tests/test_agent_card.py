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
    assert (
        session_query.params["context_semantics"]["a2a_context_id_prefix"] == SESSION_CONTEXT_PREFIX
    )
    assert (
        session_query.params["context_semantics"]["upstream_session_id_field"]
        == "metadata.opencode.session_id"
    )

    interrupt = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    assert interrupt.params["deployment_context"]["project"] == "alpha"
    assert interrupt.params["shared_workspace_across_consumers"] is True
    assert interrupt.params["tenant_isolation"] == "none"
    assert interrupt.params["metadata_namespace"] == "opencode"
    assert interrupt.params["supported_metadata"] == ["opencode.directory"]
    assert interrupt.params["context_fields"]["directory"] == "metadata.opencode.directory"


def test_agent_card_chat_examples_include_project_hint_when_configured() -> None:
    card = build_agent_card(make_settings(a2a_bearer_token="test-token", a2a_project="alpha"))
    chat_skill = next(skill for skill in card.skills if skill.id == "opencode.chat")
    assert any("project alpha" in example for example in chat_skill.examples)

from opencode_a2a_serve.app import (
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    build_agent_card,
)
from opencode_a2a_serve.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "A2A_BEARER_TOKEN": "test-token",
        "OPENCODE_BASE_URL": "http://127.0.0.1:4096",
    }
    base.update(overrides)
    return Settings(**base)


def test_agent_card_description_reflects_actual_transport_capabilities() -> None:
    card = build_agent_card(_settings())

    assert "HTTP+JSON and JSON-RPC transports" in card.description
    assert "message/send, message/stream" in card.description
    assert "tasks/get, tasks/cancel" in card.description
    assert (
        "all consumers share the same underlying OpenCode workspace/environment" in card.description
    )


def test_agent_card_injects_deployment_context_into_extensions() -> None:
    card = build_agent_card(
        _settings(
            A2A_PROJECT="alpha",
            OPENCODE_DIRECTORY="/srv/workspaces/alpha",
            OPENCODE_PROVIDER_ID="google",
            OPENCODE_MODEL_ID="gemini-2.5-flash",
            OPENCODE_AGENT="code-reviewer",
            OPENCODE_VARIANT="safe",
            A2A_ALLOW_DIRECTORY_OVERRIDE="false",
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
    assert binding.params["directory_override_enabled"] is False
    assert binding.params["shared_workspace_across_consumers"] is True
    assert binding.params["tenant_isolation"] == "none"

    session_query = ext_by_uri[SESSION_QUERY_EXTENSION_URI]
    assert session_query.params["deployment_context"]["project"] == "alpha"
    assert session_query.params["shared_workspace_across_consumers"] is True
    assert session_query.params["tenant_isolation"] == "none"


def test_agent_card_chat_examples_include_project_hint_when_configured() -> None:
    card = build_agent_card(_settings(A2A_PROJECT="alpha"))
    chat_skill = next(skill for skill in card.skills if skill.id == "opencode.chat")
    assert any("project alpha" in example for example in chat_skill.examples)

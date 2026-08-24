import json

from google.protobuf.json_format import MessageToDict

from opencode_a2a.contracts.extensions import (
    AUTHENTICATED_ONLY_EXTENSION_URIS,
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    PUBLIC_EXTENSION_URIS,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
    build_protocol_compatibility_params,
    build_service_behavior_contract_params,
)
from opencode_a2a.jsonrpc.methods import SESSION_CONTEXT_PREFIX
from opencode_a2a.server.agent_card import (
    build_agent_card,
)
from tests.support.settings import make_settings


def _security_requirements(card) -> list[dict[str, dict[str, list[str]]]]:
    return [MessageToDict(requirement)["schemes"] for requirement in card.security_requirements]


def test_agent_card_description_reflects_actual_transport_capabilities() -> None:
    card = build_agent_card(make_settings(test_bearer_token="test-token"))
    skills_by_id = {skill.id: skill for skill in card.skills}

    assert "HTTP+JSON and JSON-RPC transports" in card.description
    assert "authenticated extended Agent Card discovery" in card.description
    assert (
        "all consumers share the same underlying OpenCode workspace/environment" in card.description
    )
    assert "Single-tenant deployment" in card.description
    assert card.capabilities.streaming is True
    assert card.capabilities.extended_agent_card is True
    assert [
        (iface.protocol_binding, iface.protocol_version) for iface in card.supported_interfaces
    ] == [
        ("HTTP+JSON", "1.0"),
        ("JSONRPC", "1.0"),
    ]
    assert card.default_input_modes == ["text/plain", "application/octet-stream"]
    assert card.default_output_modes == ["text/plain", "application/json"]
    assert set(card.security_schemes.keys()) == {"bearerAuth"}
    assert _security_requirements(card) == [{"bearerAuth": {}}]
    assert set(skills_by_id.keys()) == {"opencode.chat", "opencode.interrupt.callback"}
    assert skills_by_id["opencode.chat"].input_modes == ["text/plain", "application/octet-stream"]
    assert skills_by_id["opencode.chat"].output_modes == ["text/plain", "application/json"]
    assert skills_by_id["opencode.interrupt.callback"].input_modes == ["application/json"]
    assert skills_by_id["opencode.interrupt.callback"].output_modes == ["application/json"]


def test_agent_card_declares_optional_basic_auth_when_configured() -> None:
    card = build_agent_card(
        make_settings(
            test_bearer_token="test-token",
            test_basic_username="operator",
            test_basic_password="op-pass",  # pragma: allowlist secret
        )
    )

    assert set(card.security_schemes.keys()) == {"bearerAuth", "basicAuth"}
    assert _security_requirements(card) == [{"bearerAuth": {}}, {"basicAuth": {}}]


def test_agent_card_reflects_registry_declared_auth_schemes() -> None:
    card = build_agent_card(
        make_settings(
            test_bearer_token=None,
            a2a_static_auth_credentials=(
                {
                    "scheme": "basic",
                    "username": "operator",
                    "password": "op-pass",  # pragma: allowlist secret
                },
            ),
        )
    )

    assert set(card.security_schemes.keys()) == {"basicAuth"}
    assert _security_requirements(card) == [{"basicAuth": {}}]


def test_public_agent_card_is_slimmed_but_keeps_core_shared_contract_hints() -> None:
    public_card = build_agent_card(make_settings(test_bearer_token="test-token"))
    extended_card = build_agent_card(
        make_settings(test_bearer_token="test-token"),
        include_detailed_contracts=True,
    )
    ext_by_uri = {ext.uri: ext for ext in public_card.capabilities.extensions or []}
    extended_ext_by_uri = {ext.uri: ext for ext in extended_card.capabilities.extensions or []}
    assert tuple(ext_by_uri.keys()) == PUBLIC_EXTENSION_URIS
    assert set(extended_ext_by_uri.keys()) == set(PUBLIC_EXTENSION_URIS) | set(
        AUTHENTICATED_ONLY_EXTENSION_URIS
    )

    assert ext_by_uri[SESSION_BINDING_EXTENSION_URI].params == {
        "metadata_field": "metadata.shared.session.id",
        "session_metadata_field": "metadata.shared.session",
        "behavior": "prefer_metadata_binding_else_create_session",
        "supported_metadata": [
            "shared.session.id",
            "opencode.directory",
            "opencode.workspace.id",
        ],
        "session_fields": {
            "id": "metadata.shared.session.id",
        },
        "provider_private_metadata": [
            "opencode.directory",
            "opencode.workspace.id",
        ],
    }
    assert ext_by_uri[MODEL_SELECTION_EXTENSION_URI].params == {
        "metadata_field": "metadata.shared.model",
        "behavior": "prefer_metadata_model_else_upstream_default",
        "applies_to_methods": ["SendMessage", "SendStreamingMessage"],
        "supported_metadata": [
            "shared.model.providerID",
            "shared.model.modelID",
        ],
        "provider_private_metadata": [],
        "fields": {
            "providerID": "metadata.shared.model.providerID",
            "modelID": "metadata.shared.model.modelID",
        },
    }
    assert ext_by_uri[STREAMING_EXTENSION_URI].params == {
        "artifact_metadata_field": "metadata.shared.stream",
        "progress_metadata_field": "metadata.shared.progress",
        "usage_metadata_field": "metadata.shared.usage",
        "block_types": ["text", "reasoning", "tool_call"],
        "stream_fields": {
            "block_type": "metadata.shared.stream.block_type",
            "sequence": "metadata.shared.stream.sequence",
        },
        "progress_fields": {
            "type": "metadata.shared.progress.type",
            "status": "metadata.shared.progress.status",
        },
        "usage_fields": {
            "input_tokens": "metadata.shared.usage.input_tokens",
            "output_tokens": "metadata.shared.usage.output_tokens",
            "total_tokens": "metadata.shared.usage.total_tokens",
        },
    }
    assert ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI].params == {
        "methods": {
            "reply_permission": "a2a.interrupt.permission.reply",
            "reply_question": "a2a.interrupt.question.reply",
            "reject_question": "a2a.interrupt.question.reject",
        },
        "supported_interrupt_events": [
            "permission.asked",
            "question.asked",
        ],
        "request_id_field": "metadata.shared.interrupt.request_id",
        "interrupt_metadata_field": "metadata.shared.interrupt",
        "interrupt_fields": {
            "request_id": "metadata.shared.interrupt.request_id",
            "type": "metadata.shared.interrupt.type",
            "phase": "metadata.shared.interrupt.phase",
        },
    }
    assert (
        extended_ext_by_uri[STREAMING_EXTENSION_URI].params["stream_fields"]
        == ext_by_uri[STREAMING_EXTENSION_URI].params["stream_fields"]
    )
    assert (
        extended_ext_by_uri[STREAMING_EXTENSION_URI].params["usage_fields"]
        == ext_by_uri[STREAMING_EXTENSION_URI].params["usage_fields"]
    )

    for uri in AUTHENTICATED_ONLY_EXTENSION_URIS:
        assert uri not in ext_by_uri
        assert MessageToDict(extended_ext_by_uri[uri]).get("params") not in (None, {})

    public_size = len(
        json.dumps(
            MessageToDict(public_card),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    extended_size = len(
        json.dumps(
            MessageToDict(extended_card),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert public_size < extended_size
    assert public_size < 10000


def test_agent_card_injects_profile_into_extensions() -> None:
    card = build_agent_card(
        make_settings(
            test_bearer_token="test-token",
            a2a_project="alpha",
            opencode_workspace_root="/srv/workspaces/alpha",
            opencode_agent="code-reviewer",
            opencode_variant="safe",
            a2a_allow_directory_override=False,
            a2a_sandbox_mode="workspace-write",
            a2a_sandbox_filesystem_scope="workspace_and_declared_roots",
            a2a_sandbox_writable_roots=("/srv/workspaces/alpha", "/tmp/opencode"),
            a2a_network_access="restricted",
            a2a_network_allowed_domains=("api.openai.com", "github.com"),
            a2a_approval_policy="never",
            a2a_approval_escalation_behavior="unsupported",
            a2a_write_access_scope="workspace_and_declared_roots",
            a2a_write_access_outside_workspace="disallowed",
            a2a_expose_workspace_root_in_card=True,
        ),
        include_detailed_contracts=True,
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    binding = ext_by_uri[SESSION_BINDING_EXTENSION_URI]
    profile = binding.params["profile"]
    assert binding.params["metadata_field"] == "metadata.shared.session.id"
    assert binding.params["session_metadata_field"] == "metadata.shared.session"
    assert binding.params["supported_metadata"] == [
        "shared.session.id",
        "opencode.directory",
        "opencode.workspace.id",
    ]
    assert binding.params["provider_private_metadata"] == [
        "opencode.directory",
        "opencode.workspace.id",
    ]
    assert binding.params["session_fields"] == {
        "id": "metadata.shared.session.id",
    }
    assert profile["profile_id"] == "opencode-a2a-single-tenant-coding-v1"
    assert profile["deployment"] == {
        "id": "single_tenant_shared_workspace",
        "single_tenant": True,
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
    }
    assert profile["runtime_context"] == {
        "project": "alpha",
        "workspace_root": "/srv/workspaces/alpha",
        "agent": "code-reviewer",
        "variant": "safe",
    }
    assert profile["runtime_features"]["directory_binding"] == {
        "allow_override": False,
        "scope": "workspace_root_only",
        "metadata_field": "metadata.opencode.directory",
    }
    assert profile["runtime_features"]["workspace_binding"] == {
        "enabled": True,
        "metadata_field": "metadata.opencode.workspace.id",
        "upstream_query_param": "workspace",
        "precedence": "prefer_workspace_else_directory",
    }
    assert profile["runtime_features"]["execution_environment"] == {
        "sandbox": {
            "mode": "workspace-write",
            "filesystem_scope": "workspace_and_declared_roots",
            "writable_roots": ["/srv/workspaces/alpha", "/tmp/opencode"],
        },
        "network": {
            "access": "restricted",
            "allowed_domains": ["api.openai.com", "github.com"],
        },
        "approval": {
            "policy": "never",
            "escalation_behavior": "unsupported",
        },
        "write_access": {
            "scope": "workspace_and_declared_roots",
            "outside_workspace": "disallowed",
        },
    }

    model_selection = ext_by_uri[MODEL_SELECTION_EXTENSION_URI]
    assert model_selection.params["metadata_field"] == "metadata.shared.model"
    assert model_selection.params["fields"]["providerID"] == "metadata.shared.model.providerID"
    assert model_selection.params["fields"]["modelID"] == "metadata.shared.model.modelID"
    assert model_selection.params["applies_to_methods"] == ["SendMessage", "SendStreamingMessage"]
    assert model_selection.params["behavior"] == "prefer_metadata_model_else_upstream_default"

    streaming = ext_by_uri[STREAMING_EXTENSION_URI]
    assert streaming.params["artifact_metadata_field"] == "metadata.shared.stream"
    assert streaming.params["progress_metadata_field"] == "metadata.shared.progress"
    assert streaming.params["usage_metadata_field"] == "metadata.shared.usage"
    assert streaming.params["block_types"] == ["text", "reasoning", "tool_call"]
    assert streaming.params["block_contracts"] == {
        "text": {
            "part_kind": "text",
            "payload_field": "artifact.parts[].text",
        },
        "reasoning": {
            "part_kind": "text",
            "payload_field": "artifact.parts[].text",
        },
        "tool_call": {
            "part_kind": "data",
            "payload_field": "artifact.parts[].data",
            "payload_fields": {
                "call_id": "artifact.parts[].data.call_id",
                "tool": "artifact.parts[].data.tool",
                "status": "artifact.parts[].data.status",
                "title": "artifact.parts[].data.title",
                "subtitle": "artifact.parts[].data.subtitle",
                "input": "artifact.parts[].data.input",
                "output": "artifact.parts[].data.output",
                "error": "artifact.parts[].data.error",
            },
        },
    }
    assert streaming.params["stream_fields"]["sequence"] == "metadata.shared.stream.sequence"
    assert streaming.params["progress_fields"]["type"] == "metadata.shared.progress.type"
    assert streaming.params["usage_fields"] == {
        "input_tokens": "metadata.shared.usage.input_tokens",
        "output_tokens": "metadata.shared.usage.output_tokens",
        "total_tokens": "metadata.shared.usage.total_tokens",
    }

    session_management = ext_by_uri[SESSION_MANAGEMENT_EXTENSION_URI]
    assert session_management.params["profile"]["runtime_context"]["project"] == "alpha"
    assert session_management.params["control_methods"] == {
        "prompt_async": "opencode.sessions.prompt_async",
        "command": "opencode.sessions.command",
    }
    assert session_management.params["read_methods"] == {
        "status": "opencode.sessions.status",
        "list_sessions": "opencode.sessions.list",
        "get_session": "opencode.sessions.get",
        "get_session_children": "opencode.sessions.children",
        "get_session_todo": "opencode.sessions.todo",
        "get_session_diff": "opencode.sessions.diff",
        "get_session_message": "opencode.sessions.messages.get",
        "get_session_messages": "opencode.sessions.messages.list",
    }
    assert session_management.params["mutation_methods"] == {
        "fork": "opencode.sessions.fork",
        "share": "opencode.sessions.share",
        "unshare": "opencode.sessions.unshare",
        "summarize": "opencode.sessions.summarize",
        "revert": "opencode.sessions.revert",
        "unrevert": "opencode.sessions.unrevert",
    }
    assert session_management.params["methods"]["status"] == "opencode.sessions.status"
    assert session_management.params["methods"]["get_session"] == "opencode.sessions.get"
    assert session_management.params["methods"]["prompt_async"] == "opencode.sessions.prompt_async"
    assert session_management.params["methods"]["command"] == "opencode.sessions.command"
    assert "shell" not in session_management.params["methods"]
    assert session_management.params["control_method_flags"]["opencode.sessions.shell"] == {
        "enabled_by_default": False,
        "config_key": "A2A_ENABLE_SESSION_SHELL",
    }
    assert session_management.params["pagination"]["default_limit"] == SESSION_QUERY_DEFAULT_LIMIT
    assert session_management.params["pagination"]["max_limit"] == SESSION_QUERY_MAX_LIMIT
    assert session_management.params["pagination"]["cursor_param"] == "before"
    assert session_management.params["pagination"]["result_cursor_field"] == "next_cursor"
    assert session_management.params["pagination"]["applies_to"] == [
        "opencode.sessions.list",
        "opencode.sessions.messages.list",
    ]
    assert session_management.params["pagination"]["cursor_applies_to"] == [
        "opencode.sessions.messages.list"
    ]
    prompt_contract = session_management.params["method_contracts"][
        "opencode.sessions.prompt_async"
    ]
    command_contract = session_management.params["method_contracts"]["opencode.sessions.command"]
    status_contract = session_management.params["method_contracts"]["opencode.sessions.status"]
    get_contract = session_management.params["method_contracts"]["opencode.sessions.get"]
    diff_contract = session_management.params["method_contracts"]["opencode.sessions.diff"]
    message_get_contract = session_management.params["method_contracts"][
        "opencode.sessions.messages.get"
    ]
    fork_contract = session_management.params["method_contracts"]["opencode.sessions.fork"]
    summarize_contract = session_management.params["method_contracts"][
        "opencode.sessions.summarize"
    ]
    revert_contract = session_management.params["method_contracts"]["opencode.sessions.revert"]
    unrevert_contract = session_management.params["method_contracts"]["opencode.sessions.unrevert"]
    list_contract = session_management.params["method_contracts"]["opencode.sessions.list"]
    messages_contract = session_management.params["method_contracts"][
        "opencode.sessions.messages.list"
    ]
    assert status_contract["result"]["fields"] == ["items"]
    assert get_contract["params"]["required"] == ["session_id"]
    assert get_contract["result"]["fields"] == ["item"]
    assert diff_contract["params"]["optional"] == [
        "message_id",
        "directory",
        "metadata.opencode.directory",
        "metadata.opencode.workspace.id",
    ]
    assert message_get_contract["params"]["required"] == ["session_id", "message_id"]
    assert fork_contract["params"]["optional"] == [
        "request.messageID",
        "directory",
        "metadata.opencode.directory",
        "metadata.opencode.workspace.id",
    ]
    assert summarize_contract["params"]["optional"] == [
        "request.providerID",
        "request.modelID",
        "request.auto",
        "directory",
        "metadata.opencode.directory",
        "metadata.opencode.workspace.id",
    ]
    assert summarize_contract["result"]["fields"] == ["ok", "session_id"]
    assert revert_contract["params"]["required"] == ["session_id", "request.messageID"]
    assert revert_contract["params"]["optional"] == [
        "request.partID",
        "directory",
        "metadata.opencode.directory",
        "metadata.opencode.workspace.id",
    ]
    assert revert_contract["result"]["items_type"] == "SessionSummary"
    assert unrevert_contract["result"]["fields"] == ["item"]
    assert prompt_contract["params"]["required"] == ["session_id", "request.parts"]
    assert prompt_contract["request_parts"] == {
        "items_type": "PromptAsyncPart[]",
        "type_field": "type",
        "accepted_types": ["text", "file", "agent", "subtask"],
        "part_contracts": {
            "text": {"required": ["type", "text"]},
            "file": {"required": ["type", "mime", "url"]},
            "agent": {"required": ["type", "name"]},
            "subtask": {
                "required": ["type", "prompt", "description", "agent"],
                "optional": ["model", "command"],
            },
        },
    }
    assert prompt_contract["subtask_support"] == {
        "support_level": "passthrough-compatible",
        "invocation_path": "request.parts[]",
        "part_type": "subtask",
        "subagent_selector_field": "request.parts[].agent",
        "execution_model": "upstream-provider-private-subagent-runtime",
        "notes": [
            (
                "opencode-a2a validates and forwards provider-private subtask parts to "
                "the upstream OpenCode session runtime."
            ),
            (
                "The adapter does not define a separate subagent discovery or "
                "orchestration JSON-RPC method surface."
            ),
            (
                "Subtask execution semantics, available subagent names, and any task-tool "
                "fan-out remain upstream OpenCode behavior."
            ),
        ],
    }
    assert prompt_contract["result"]["fields"] == ["ok", "session_id"]
    assert command_contract["params"]["required"] == [
        "session_id",
        "request.command",
        "request.arguments",
    ]
    assert command_contract["result"]["fields"] == ["item"]
    assert list_contract["params"]["optional"] == [
        "limit",
        "directory",
        "metadata.opencode.workspace.id",
        "roots",
        "start",
        "search",
    ]
    assert messages_contract["params"]["optional"] == [
        "limit",
        "before",
        "metadata.opencode.workspace.id",
    ]
    assert messages_contract["result"]["fields"] == ["items", "next_cursor"]
    assert list_contract["notification_response_status"] == 204
    assert messages_contract["notification_response_status"] == 204
    assert summarize_contract["notification_response_status"] == 204
    assert revert_contract["notification_response_status"] == 204
    assert unrevert_contract["notification_response_status"] == 204
    assert prompt_contract["notification_response_status"] == 204
    assert "result_envelope" not in session_management.params
    assert "opencode.sessions.shell" not in session_management.params["method_contracts"]
    assert (
        session_management.params["context_semantics"]["a2a_context_id_prefix"]
        == SESSION_CONTEXT_PREFIX
    )
    assert (
        session_management.params["context_semantics"]["upstream_session_id_field"]
        == "metadata.shared.session.id"
    )
    assert session_management.params["errors"]["business_codes"] == {
        "SESSION_NOT_FOUND": -32001,
        "SESSION_FORBIDDEN": -32006,
        "AUTHORIZATION_FORBIDDEN": -32007,
        "UPSTREAM_UNREACHABLE": -32002,
        "UPSTREAM_HTTP_ERROR": -32003,
        "UPSTREAM_PAYLOAD_ERROR": -32005,
    }
    assert session_management.params["errors"]["error_data_fields"] == [
        "type",
        "method",
        "session_id",
        "capability",
        "credential_id",
        "upstream_status",
        "detail",
    ]
    assert session_management.params["errors"]["invalid_params_data_fields"] == [
        "type",
        "field",
        "fields",
        "supported",
        "unsupported",
    ]

    provider_discovery = ext_by_uri[PROVIDER_DISCOVERY_EXTENSION_URI]
    assert provider_discovery.params["profile"]["runtime_context"]["project"] == "alpha"
    assert provider_discovery.params["methods"] == {
        "list_providers": "opencode.providers.list",
        "list_models": "opencode.models.list",
    }
    assert provider_discovery.params["supported_metadata"] == [
        "opencode.directory",
        "opencode.workspace.id",
    ]
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

    workspace_control = ext_by_uri[WORKSPACE_CONTROL_EXTENSION_URI]
    assert workspace_control.params["profile"]["runtime_context"]["project"] == "alpha"
    assert workspace_control.params["methods"] == {
        "list_projects": "opencode.projects.list",
        "get_current_project": "opencode.projects.current",
        "list_workspaces": "opencode.workspaces.list",
        "list_worktrees": "opencode.worktrees.list",
    }
    assert workspace_control.params["control_method_flags"]["opencode.workspaces.create"] == {
        "enabled_by_default": False,
        "config_key": "A2A_ENABLE_WORKSPACE_MUTATIONS",
    }
    assert workspace_control.params["upstream_stability"] == {
        "opencode.projects.list": "stable",
        "opencode.projects.current": "stable",
        "opencode.workspaces.list": "experimental",
        "opencode.worktrees.list": "experimental",
        "opencode.workspaces.create": "experimental",
        "opencode.workspaces.remove": "experimental",
        "opencode.worktrees.create": "experimental",
        "opencode.worktrees.remove": "experimental",
        "opencode.worktrees.reset": "experimental",
    }
    assert workspace_control.params["routing_fields"]["workspace_id"] == (
        "metadata.opencode.workspace.id"
    )
    assert workspace_control.params["method_contracts"]["opencode.projects.list"]["result"] == {
        "fields": ["items"],
        "items_type": "Project[]",
    }
    assert "opencode.workspaces.create" not in workspace_control.params["method_contracts"]
    assert "opencode.worktrees.reset" not in workspace_control.params["method_contracts"]

    interrupt_recovery = ext_by_uri[INTERRUPT_RECOVERY_EXTENSION_URI]
    assert interrupt_recovery.params["profile"]["runtime_context"]["project"] == "alpha"
    assert interrupt_recovery.params["methods"] == {
        "list_permissions": "opencode.permissions.list",
        "list_questions": "opencode.questions.list",
    }
    assert interrupt_recovery.params["method_contracts"]["opencode.permissions.list"]["result"] == {
        "fields": ["items"],
        "items_type": "InterruptRequest[]",
    }
    assert interrupt_recovery.params["recovery_scope"] == {
        "data_source": "local_interrupt_binding_registry",
        "identity_scope": "current_authenticated_caller",
        "empty_result_when_identity_unavailable": True,
    }
    assert interrupt_recovery.params["item_fields"]["details"] == "items[].details"
    assert interrupt_recovery.params["errors"]["invalid_params_data_fields"] == [
        "type",
        "field",
        "fields",
    ]

    interrupt = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    assert interrupt.params["profile"]["runtime_context"]["project"] == "alpha"
    assert interrupt.params["request_id_field"] == "metadata.shared.interrupt.request_id"
    assert interrupt.params["interrupt_metadata_field"] == "metadata.shared.interrupt"
    assert interrupt.params["interrupt_fields"] == {
        "request_id": "metadata.shared.interrupt.request_id",
        "type": "metadata.shared.interrupt.type",
        "phase": "metadata.shared.interrupt.phase",
        "details": "metadata.shared.interrupt.details",
        "resolution": "metadata.shared.interrupt.resolution",
    }
    assert interrupt.params["supported_metadata"] == [
        "opencode.directory",
        "opencode.workspace.id",
    ]
    assert interrupt.params["provider_private_metadata"] == [
        "opencode.directory",
        "opencode.workspace.id",
    ]
    assert interrupt.params["context_fields"]["directory"] == "metadata.opencode.directory"
    assert interrupt.params["context_fields"]["workspace_id"] == "metadata.opencode.workspace.id"
    assert interrupt.params["errors"]["business_codes"] == {
        "INTERRUPT_REQUEST_NOT_FOUND": -32004,
        "INTERRUPT_REQUEST_EXPIRED": -32007,
        "INTERRUPT_TYPE_MISMATCH": -32008,
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
    assert interrupt.params["errors"]["error_data_fields"] == [
        "type",
        "request_id",
        "expected_interrupt_type",
        "actual_interrupt_type",
        "upstream_status",
        "detail",
    ]
    assert interrupt.params["errors"]["invalid_params_data_fields"] == [
        "type",
        "field",
        "fields",
        "request_id",
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
    expected_service_behaviors = build_service_behavior_contract_params()
    expected_protocol_compatibility = build_protocol_compatibility_params(
        supported_protocol_versions=["1.0"],
        default_protocol_version="1.0",
    )
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
    assert compatibility.params["extension_retention"][WORKSPACE_CONTROL_EXTENSION_URI] == {
        "surface": "jsonrpc-extension",
        "availability": "always",
        "retention": "mixed",
        "upstream_stability": "mixed",
    }
    assert compatibility.params["extension_retention"][INTERRUPT_RECOVERY_EXTENSION_URI] == {
        "surface": "jsonrpc-extension",
        "availability": "always",
        "retention": "stable",
        "implementation_scope": "adapter-local",
        "identity_scope": "current_authenticated_caller",
    }
    shell_policy = compatibility.params["method_retention"]["opencode.sessions.shell"]
    workspace_list_policy = compatibility.params["method_retention"]["opencode.workspaces.list"]
    workspace_mutation_policy = compatibility.params["method_retention"][
        "opencode.workspaces.create"
    ]
    interrupt_recovery_policy = compatibility.params["method_retention"][
        "opencode.permissions.list"
    ]
    assert compatibility.params["deployment"]["id"] == "single_tenant_shared_workspace"
    assert compatibility.params["runtime_features"]["session_shell"]["availability"] == "disabled"
    assert compatibility.params["runtime_features"]["workspace_mutations"]["availability"] == (
        "disabled"
    )
    assert shell_policy["availability"] == "disabled"
    assert shell_policy["retention"] == "deployment-conditional"
    assert shell_policy["toggle"] == "A2A_ENABLE_SESSION_SHELL"
    assert workspace_list_policy == {
        "surface": "extension",
        "availability": "always",
        "retention": "experimental-upstream",
        "extension_uri": WORKSPACE_CONTROL_EXTENSION_URI,
    }
    assert workspace_mutation_policy["availability"] == "disabled"
    assert workspace_mutation_policy["retention"] == "deployment-conditional"
    assert workspace_mutation_policy["toggle"] == "A2A_ENABLE_WORKSPACE_MUTATIONS"
    assert workspace_mutation_policy["upstream_stability"] == "experimental"
    assert interrupt_recovery_policy == {
        "surface": "extension",
        "availability": "always",
        "retention": "stable",
        "extension_uri": INTERRUPT_RECOVERY_EXTENSION_URI,
        "implementation_scope": "adapter-local",
        "identity_scope": "current_authenticated_caller",
    }
    assert compatibility.params["method_retention"]["GetExtendedAgentCard"] == {
        "surface": "core",
        "availability": "always",
        "retention": "required",
    }
    assert compatibility.params["method_retention"]["GetTaskPushNotificationConfig"] == {
        "surface": "core",
        "availability": "always",
        "retention": "required",
    }
    assert compatibility.params["service_behaviors"] == expected_service_behaviors
    assert compatibility.params["service_behaviors"]["classification"] == (
        "service-level-semantic-enhancement"
    )
    assert compatibility.params["service_behaviors"]["methods"]["CancelTask"]["idempotency"] == {
        "already_canceled": {
            "behavior": "return_current_terminal_task",
            "returns_current_state": "canceled",
            "error": None,
        }
    }
    assert compatibility.params["service_behaviors"]["methods"]["SubscribeToTask"][
        "terminal_state_behavior"
    ] == {
        "behavior": "reject_with_unsupported_operation",
        "error": "UnsupportedOperationError",
    }
    assert compatibility.params["protocol_compatibility"] == expected_protocol_compatibility
    assert compatibility.description.endswith("deployment-conditional methods.")

    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert wire_contract.params["profile"]["profile_id"] == "opencode-a2a-single-tenant-coding-v1"
    assert wire_contract.params["default_protocol_version"] == "1.0"
    assert wire_contract.params["supported_protocol_versions"] == ["1.0"]
    assert wire_contract.params["protocol_compatibility"] == expected_protocol_compatibility
    assert MODEL_SELECTION_EXTENSION_URI in wire_contract.params["extensions"]["extension_uris"]
    assert PROVIDER_DISCOVERY_EXTENSION_URI in wire_contract.params["extensions"]["extension_uris"]
    assert WORKSPACE_CONTROL_EXTENSION_URI in wire_contract.params["extensions"]["extension_uris"]
    assert INTERRUPT_RECOVERY_EXTENSION_URI in wire_contract.params["extensions"]["extension_uris"]
    assert "GetExtendedAgentCard" in wire_contract.params["all_jsonrpc_methods"]
    assert "GetTaskPushNotificationConfig" in wire_contract.params["all_jsonrpc_methods"]
    assert "GET /tasks" in wire_contract.params["core"]["http_endpoints"]
    assert "GET /extendedAgentCard" in wire_contract.params["core"]["http_endpoints"]
    assert (
        "GET /tasks/{id}/pushNotificationConfigs" in wire_contract.params["core"]["http_endpoints"]
    )
    assert "opencode.sessions.shell" not in wire_contract.params["all_jsonrpc_methods"]
    assert wire_contract.params["service_behaviors"] == expected_service_behaviors
    assert wire_contract.params["extensions"]["conditionally_available_methods"] == {
        "opencode.sessions.shell": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_SESSION_SHELL",
        },
        "opencode.workspaces.create": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.workspaces.remove": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.worktrees.create": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.worktrees.remove": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.worktrees.reset": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
    }
    assert wire_contract.description.endswith("unified error contracts.")


def test_agent_card_omits_workspace_root_by_default() -> None:
    card = build_agent_card(
        make_settings(
            test_bearer_token="test-token",
            a2a_project="alpha",
            opencode_workspace_root="/srv/workspaces/alpha",
        ),
        include_detailed_contracts=True,
    )

    assert "Workspace root" not in card.description
    assert "/srv/workspaces/alpha" not in card.description
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}
    profile = ext_by_uri[SESSION_BINDING_EXTENSION_URI].params["profile"]
    assert profile["runtime_context"] == {"project": "alpha"}


def test_agent_card_chat_examples_include_project_hint_when_configured() -> None:
    card = build_agent_card(make_settings(test_bearer_token="test-token", a2a_project="alpha"))
    chat_skill = next(skill for skill in card.skills if skill.id == "opencode.chat")
    assert list(chat_skill.examples) == []
    assert "shared session binding" in chat_skill.description
    assert "text/plain responses" in chat_skill.description
    assert "core-a2a" in chat_skill.tags
    assert "portable" in chat_skill.tags


def test_agent_card_contracts_include_shell_when_enabled() -> None:
    card = build_agent_card(
        make_settings(test_bearer_token="test-token", a2a_enable_session_shell=True),
        include_detailed_contracts=True,
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_management = ext_by_uri[SESSION_MANAGEMENT_EXTENSION_URI]
    assert session_management.params["control_methods"]["shell"] == "opencode.sessions.shell"
    assert session_management.params["methods"]["shell"] == "opencode.sessions.shell"
    assert "opencode.sessions.shell" in session_management.params["method_contracts"]

    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    shell_policy = compatibility.params["method_retention"]["opencode.sessions.shell"]
    assert compatibility.params["runtime_features"]["session_shell"]["availability"] == "enabled"
    assert shell_policy["availability"] == "enabled"

    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert wire_contract.params["profile"]["runtime_features"]["session_shell"]["availability"] == (
        "enabled"
    )
    assert "opencode.sessions.shell" in wire_contract.params["all_jsonrpc_methods"]
    assert wire_contract.params["extensions"]["conditionally_available_methods"] == {
        "opencode.workspaces.create": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.workspaces.remove": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.worktrees.create": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.worktrees.remove": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
        "opencode.worktrees.reset": {
            "reason": "disabled_by_configuration",
            "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
        },
    }

    session_skill = next(
        skill for skill in card.skills if skill.id == "opencode.sessions.management"
    )
    assert any("opencode.sessions.shell" in example for example in session_skill.examples)


def test_agent_card_contracts_include_workspace_mutations_when_enabled() -> None:
    card = build_agent_card(
        make_settings(test_bearer_token="test-token", a2a_enable_workspace_mutations=True),
        include_detailed_contracts=True,
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    workspace_control = ext_by_uri[WORKSPACE_CONTROL_EXTENSION_URI]
    assert workspace_control.params["methods"]["create_workspace"] == "opencode.workspaces.create"
    assert workspace_control.params["methods"]["create_worktree"] == "opencode.worktrees.create"
    assert "opencode.workspaces.create" in workspace_control.params["method_contracts"]
    assert "opencode.worktrees.reset" in workspace_control.params["method_contracts"]
    assert (
        workspace_control.params["upstream_stability"]["opencode.workspaces.create"]
        == "experimental"
    )

    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    workspace_mutation_policy = compatibility.params["method_retention"][
        "opencode.workspaces.create"
    ]
    assert compatibility.params["runtime_features"]["workspace_mutations"]["availability"] == (
        "enabled"
    )
    assert workspace_mutation_policy["availability"] == "enabled"

    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    assert (
        wire_contract.params["profile"]["runtime_features"]["workspace_mutations"]["availability"]
        == "enabled"
    )
    assert "opencode.workspaces.create" in wire_contract.params["all_jsonrpc_methods"]
    assert "opencode.worktrees.reset" in wire_contract.params["all_jsonrpc_methods"]


def test_agent_card_skills_hide_shell_when_disabled_by_default() -> None:
    card = build_agent_card(make_settings(test_bearer_token="test-token"))
    skill_ids = {skill.id for skill in card.skills}

    assert skill_ids == {"opencode.chat", "opencode.interrupt.callback"}
    assert "opencode.sessions.management" not in skill_ids
    assert "opencode.providers.query" not in skill_ids
    assert "opencode.workspace.control" not in skill_ids
    assert "opencode.interrupt.recovery" not in skill_ids


def test_agent_card_hides_shell_when_policy_disables_it() -> None:
    card = build_agent_card(
        make_settings(
            test_bearer_token="test-token",
            a2a_enable_session_shell=True,
            a2a_sandbox_mode="read-only",
            a2a_write_access_scope="workspace_only",
        ),
        include_detailed_contracts=True,
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_management = ext_by_uri[SESSION_MANAGEMENT_EXTENSION_URI]
    compatibility = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]

    assert "shell" not in session_management.params["methods"]
    assert "opencode.sessions.shell" not in session_management.params["method_contracts"]
    assert compatibility.params["runtime_features"]["session_shell"]["availability"] == "disabled"

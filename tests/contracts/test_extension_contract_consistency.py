import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from opencode_a2a.contracts.extensions import (
    ALL_EXTENSION_URIS,
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    EXTENSION_SPEC_DOCUMENT_PATHS_BY_URI,
    EXTENSION_URI_NAMESPACE,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
    build_capability_snapshot,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_interrupt_recovery_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_public_interrupt_callback_extension_params,
    build_public_session_binding_extension_params,
    build_public_streaming_extension_params,
    build_session_binding_extension_params,
    build_session_management_extension_params,
    build_streaming_extension_params,
    build_wire_contract_params,
    build_workspace_control_extension_params,
    select_public_extension_params,
)
from opencode_a2a.jsonrpc.methods import SESSION_CONTEXT_PREFIX
from opencode_a2a.profile.runtime import build_runtime_profile
from opencode_a2a.protocol_versions import A2A_PROTOCOL_VERSION
from opencode_a2a.server.agent_card import build_agent_card
from opencode_a2a.server.application import create_app
from tests.support.session_extensions import _extension_headers
from tests.support.session_query_client import (
    DummySessionQueryOpencodeUpstreamClient as DummyOpencodeUpstreamClient,
)
from tests.support.settings import make_settings


@pytest.mark.parametrize(
    ("builder", "kwargs"),
    [
        pytest.param(
            build_compatibility_profile_params,
            {"protocol_version": "2.0"},
            id="compatibility-profile-protocol-version",
        ),
        pytest.param(
            build_compatibility_profile_params,
            {"protocol_version": "invalid"},
            id="compatibility-profile-invalid-protocol-version",
        ),
        pytest.param(
            build_compatibility_profile_params,
            {"default_protocol_version": "2.0"},
            id="compatibility-profile-default-protocol-version",
        ),
        pytest.param(
            build_compatibility_profile_params,
            {"supported_protocol_versions": ["1.0", "2.0"]},
            id="compatibility-profile-supported-protocol-versions",
        ),
        pytest.param(
            build_wire_contract_params,
            {"protocol_version": "2.0"},
            id="wire-contract-protocol-version",
        ),
        pytest.param(
            build_wire_contract_params,
            {"protocol_version": "invalid"},
            id="wire-contract-invalid-protocol-version",
        ),
        pytest.param(
            build_wire_contract_params,
            {"default_protocol_version": "2.0"},
            id="wire-contract-default-protocol-version",
        ),
        pytest.param(
            build_wire_contract_params,
            {"supported_protocol_versions": ["1.0", "2.0"]},
            id="wire-contract-supported-protocol-versions",
        ),
    ],
)
def test_provider_private_contract_builders_reject_non_1_0_protocol_lines(
    builder,
    kwargs: dict[str, object],
) -> None:
    runtime_profile = build_runtime_profile(make_settings(test_bearer_token="test-token"))
    call_kwargs = {
        "protocol_version": A2A_PROTOCOL_VERSION,
        "runtime_profile": runtime_profile,
        **kwargs,
    }

    with pytest.raises(ValueError, match="provider-private A2A protocol version '1\\.0'"):
        builder(**call_kwargs)


@pytest.mark.parametrize(
    "builder",
    [
        build_compatibility_profile_params,
        build_wire_contract_params,
    ],
)
def test_provider_private_contract_builders_canonicalize_1_0_protocol_inputs(builder) -> None:
    runtime_profile = build_runtime_profile(make_settings(test_bearer_token="test-token"))

    params = builder(
        protocol_version="1.0.0",
        default_protocol_version="1.0.0",
        supported_protocol_versions=["1.0.0"],
        runtime_profile=runtime_profile,
    )

    assert params["default_protocol_version"] == A2A_PROTOCOL_VERSION
    assert params["supported_protocol_versions"] == [A2A_PROTOCOL_VERSION]
    if builder is build_wire_contract_params:
        assert params["protocol_version"] == A2A_PROTOCOL_VERSION
        assert params["profile"]["protocol_version"] == A2A_PROTOCOL_VERSION
    else:
        assert params["protocol_version"] == A2A_PROTOCOL_VERSION


def test_extension_uris_map_to_repository_spec_documents() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    index_path = repo_root / "docs" / "extension-specifications.md"
    index_text = index_path.read_text(encoding="utf-8")

    assert ALL_EXTENSION_URIS == (
        "urn:opencode-a2a:extension:session-binding:v1",
        "urn:opencode-a2a:extension:model-selection:v1",
        "urn:opencode-a2a:extension:stream-hints:v1",
        "urn:opencode-a2a:extension:interactive-interrupt:v1",
        "urn:opencode-a2a:extension:session-management:v1",
        "urn:opencode-a2a:extension:provider-discovery:v1",
        "urn:opencode-a2a:extension:workspace-control:v1",
        "urn:opencode-a2a:extension:interrupt-recovery:v1",
        "urn:opencode-a2a:extension:compatibility-profile:v1",
        "urn:opencode-a2a:extension:wire-contract:v1",
    )
    spec_paths = {repo_root / path for path in EXTENSION_SPEC_DOCUMENT_PATHS_BY_URI.values()}
    assert spec_paths == {index_path}

    for uri in ALL_EXTENSION_URIS:
        assert uri.startswith(EXTENSION_URI_NAMESPACE), (
            "Extension URI drifted away from the permanent URN namespace."
        )
        assert ":shared:" not in uri
        assert ":private:" not in uri
        local_spec_path = repo_root / EXTENSION_SPEC_DOCUMENT_PATHS_BY_URI[uri]
        assert local_spec_path.is_file(), (
            f"Extension URI {uri!r} does not map to a checked-in spec document."
        )
        assert uri in index_text

    assert "urn:opencode-a2a:extension:shared:" not in index_text
    assert "urn:opencode-a2a:extension:private:" not in index_text


def test_extension_ssot_matches_agent_card_contracts() -> None:
    card = build_agent_card(
        make_settings(test_bearer_token="test-token"),
        include_detailed_contracts=True,
    )
    ext_by_uri = {ext.uri: ext for ext in card.capabilities.extensions or []}

    session_binding = ext_by_uri[SESSION_BINDING_EXTENSION_URI]
    model_selection = ext_by_uri[MODEL_SELECTION_EXTENSION_URI]
    streaming = ext_by_uri[STREAMING_EXTENSION_URI]
    session_management = ext_by_uri[SESSION_MANAGEMENT_EXTENSION_URI]
    provider_discovery = ext_by_uri[PROVIDER_DISCOVERY_EXTENSION_URI]
    workspace_control = ext_by_uri[WORKSPACE_CONTROL_EXTENSION_URI]
    interrupt_recovery = ext_by_uri[INTERRUPT_RECOVERY_EXTENSION_URI]
    interrupt_callback = ext_by_uri[INTERRUPT_CALLBACK_EXTENSION_URI]
    compatibility_profile = ext_by_uri[COMPATIBILITY_PROFILE_EXTENSION_URI]
    wire_contract = ext_by_uri[WIRE_CONTRACT_EXTENSION_URI]
    settings = make_settings(test_bearer_token="test-token")
    runtime_profile = build_runtime_profile(settings)
    expected_session_binding = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_model_selection = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_streaming = build_streaming_extension_params()
    expected_session_management = build_session_management_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    expected_provider_discovery = build_provider_discovery_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_workspace_control = build_workspace_control_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_interrupt_recovery = build_interrupt_recovery_extension_params(
        runtime_profile=runtime_profile,
    )
    assert expected_session_management["pagination"]["default_limit"] == SESSION_QUERY_DEFAULT_LIMIT
    assert expected_session_management["pagination"]["max_limit"] == SESSION_QUERY_MAX_LIMIT
    expected_interrupt_callback = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    expected_compatibility_profile = build_compatibility_profile_params(
        protocol_version=A2A_PROTOCOL_VERSION,
        runtime_profile=runtime_profile,
    )
    expected_wire_contract = build_wire_contract_params(
        protocol_version=A2A_PROTOCOL_VERSION,
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
    assert session_management.params == expected_session_management, (
        "Session management extension drifted from contracts.extensions SSOT."
    )
    assert provider_discovery.params == expected_provider_discovery, (
        "Provider discovery extension drifted from contracts.extensions SSOT."
    )
    assert workspace_control.params == expected_workspace_control, (
        "Workspace control extension drifted from contracts.extensions SSOT."
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
    assert (
        compatibility_profile.params["protocol_compatibility"]
        == wire_contract.params["protocol_compatibility"]
    ), "Protocol compatibility summary drifted between compatibility profile and wire contract."


def test_openapi_jsonrpc_contract_extension_matches_public_disclosure_policy() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    openapi = app.openapi()
    post = openapi["paths"]["/"]["post"]

    contract = post.get("x-a2a-extension-contracts")
    assert isinstance(contract, dict), (
        "POST / OpenAPI is missing x-a2a-extension-contracts metadata."
    )

    session_binding = contract["session_binding"]
    model_selection = contract["model_selection"]
    streaming = contract["streaming"]
    interrupt_callback = contract["interrupt_callback"]
    assert set(contract.keys()) == {
        "session_binding",
        "model_selection",
        "streaming",
        "interrupt_callback",
    }
    settings = make_settings(test_bearer_token="test-token")
    runtime_profile = build_runtime_profile(settings)
    expected_session_binding = build_public_session_binding_extension_params(
        build_session_binding_extension_params(runtime_profile=runtime_profile),
    )
    expected_session_binding = {
        "extension_uri": SESSION_BINDING_EXTENSION_URI,
        **expected_session_binding,
    }
    expected_model_selection = select_public_extension_params(
        build_model_selection_extension_params(runtime_profile=runtime_profile),
        keys=(
            "metadata_field",
            "behavior",
            "applies_to_methods",
            "supported_metadata",
            "provider_private_metadata",
            "fields",
        ),
    )
    expected_model_selection = {
        "extension_uri": MODEL_SELECTION_EXTENSION_URI,
        **expected_model_selection,
    }
    expected_streaming = build_public_streaming_extension_params(build_streaming_extension_params())
    expected_streaming = {
        "extension_uri": STREAMING_EXTENSION_URI,
        **expected_streaming,
    }
    expected_interrupt_callback = build_public_interrupt_callback_extension_params(
        build_interrupt_callback_extension_params(runtime_profile=runtime_profile),
    )
    expected_interrupt_callback = {
        "extension_uri": INTERRUPT_CALLBACK_EXTENSION_URI,
        **expected_interrupt_callback,
    }

    assert session_binding == expected_session_binding, (
        "OpenAPI public session binding contract drifted from disclosure policy."
    )
    assert model_selection == expected_model_selection, (
        "OpenAPI public model selection contract drifted from disclosure policy."
    )
    assert streaming == expected_streaming, (
        "OpenAPI public streaming contract drifted from disclosure policy."
    )
    assert interrupt_callback == expected_interrupt_callback, (
        "OpenAPI public interrupt callback contract drifted from disclosure policy."
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
    assert {"SendMessage", "SendStreamingMessage"} <= example_methods
    assert set(INTERRUPT_CALLBACK_METHODS.values()) <= example_methods
    assert "opencode.sessions.list" not in example_methods
    assert "opencode.providers.list" not in example_methods
    assert "opencode.projects.list" not in example_methods
    assert "opencode.permissions.list" not in example_methods


def test_openapi_jsonrpc_examples_cover_shared_discovery_paths() -> None:
    app = create_app(make_settings(test_bearer_token="test-token"))
    examples = app.openapi()["paths"]["/"]["post"]["requestBody"]["content"]["application/json"][
        "examples"
    ]

    assert examples["message_send_model_override"]["value"]["params"]["metadata"]["shared"] == {
        "model": {
            "providerID": "google",
            "modelID": "gemini-2.5-flash",
        }
    }
    assert examples["message_send_session_binding"]["value"]["params"]["metadata"]["shared"] == {
        "session": {"id": "s-1"}
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("session_shell_enabled", [False, True])
@pytest.mark.parametrize("workspace_mutations_enabled", [False, True])
async def test_runtime_supported_methods_align_with_capability_snapshot(
    session_shell_enabled: bool,
    workspace_mutations_enabled: bool,
) -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_enable_session_shell=session_shell_enabled,
        a2a_enable_workspace_mutations=workspace_mutations_enabled,
    )
    app = create_app(settings)
    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    wire_contract = build_wire_contract_params(
        protocol_version=A2A_PROTOCOL_VERSION,
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
    assert error["data"]["supportedMethods"] == capability_snapshot.supported_jsonrpc_methods()
    assert error["data"]["supportedMethods"] == wire_contract["all_jsonrpc_methods"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "interrupt_type"),
    [
        ("opencode.sessions.status", {}, None),
        ("opencode.sessions.get", {"session_id": "s-1"}, None),
        ("opencode.sessions.children", {"session_id": "s-1"}, None),
        ("opencode.sessions.todo", {"session_id": "s-1"}, None),
        ("opencode.sessions.diff", {"session_id": "s-1", "message_id": "msg-1"}, None),
        ("opencode.sessions.list", {}, None),
        ("opencode.sessions.messages.get", {"session_id": "s-1", "message_id": "msg-1"}, None),
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
            "opencode.sessions.fork",
            {"session_id": "s-1", "request": {"messageID": "msg-1"}},
            None,
        ),
        ("opencode.sessions.share", {"session_id": "s-1"}, None),
        ("opencode.sessions.unshare", {"session_id": "s-1"}, None),
        (
            "opencode.sessions.summarize",
            {
                "session_id": "s-1",
                "request": {"providerID": "openai", "modelID": "gpt-5", "auto": True},
            },
            None,
        ),
        (
            "opencode.sessions.revert",
            {"session_id": "s-1", "request": {"messageID": "msg-1"}},
            None,
        ),
        ("opencode.sessions.unrevert", {"session_id": "s-1"}, None),
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
        ("opencode.projects.list", {}, None),
        ("opencode.projects.current", {}, None),
        ("opencode.workspaces.list", {}, None),
        ("opencode.workspaces.create", {"request": {"type": "git"}}, None),
        ("opencode.workspaces.remove", {"workspace_id": "wrk-1"}, None),
        ("opencode.worktrees.list", {}, None),
        ("opencode.worktrees.create", {"request": {"name": "feature-branch"}}, None),
        ("opencode.worktrees.remove", {"request": {"directory": "/tmp/worktree"}}, None),
        ("opencode.worktrees.reset", {"request": {"directory": "/tmp/worktree"}}, None),
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
        make_settings(test_bearer_token="t-1", a2a_log_payloads=False)
    )
    if interrupt_type is not None:
        request_id = params["request_id"]
        assert isinstance(request_id, str)
        await dummy.remember_interrupt_request(
            request_id=request_id,
            session_id="s-1",
            interrupt_type=interrupt_type,
        )

    monkeypatch.setattr(
        app_module,
        "OpencodeUpstreamClient",
        lambda _settings, **_kwargs: dummy,
    )
    app = app_module.create_app(make_settings(test_bearer_token="t-1", a2a_log_payloads=False))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            headers=_extension_headers({"Authorization": "Bearer t-1"}),
            json={"jsonrpc": "2.0", "method": method, "params": params},
        )
    assert response.status_code == 204


def test_server_application_imports_in_fresh_interpreter() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath_entries = [str(repo_root / "src")]
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    result = subprocess.run(
        [sys.executable, "-c", "import opencode_a2a.server.application"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(pythonpath_entries),
        },
    )

    assert result.returncode == 0, result.stderr

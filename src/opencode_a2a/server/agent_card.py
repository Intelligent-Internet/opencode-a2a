from __future__ import annotations

from typing import Any, cast

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
)

from ..auth import has_configured_auth_scheme
from ..config import Settings
from ..contracts.extensions import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_MANAGEMENT_EXTENSION_URI,
    SESSION_METHODS,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
    JsonRpcCapabilitySnapshot,
    build_capability_snapshot,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_interrupt_recovery_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_session_binding_extension_params,
    build_session_management_extension_params,
    build_streaming_extension_params,
    build_wire_contract_params,
    build_workspace_control_extension_params,
)
from ..jsonrpc.application import SESSION_CONTEXT_PREFIX
from ..profile.runtime import RuntimeProfile, build_runtime_profile

_CHAT_INPUT_MODES = ["text/plain", "application/octet-stream"]
_CHAT_OUTPUT_MODES = ["text/plain", "application/json"]
_JSON_RPC_MODES = ["application/json"]


def _select_public_extension_params(
    params: dict[str, Any],
    *,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {key: params[key] for key in keys if key in params}


def _build_public_streaming_extension_params(
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifact_metadata_field": params["artifact_metadata_field"],
        "progress_metadata_field": params["progress_metadata_field"],
        "interrupt_metadata_field": params["interrupt_metadata_field"],
        "session_metadata_field": params["session_metadata_field"],
        "usage_metadata_field": params["usage_metadata_field"],
        "block_types": params["block_types"],
        "stream_fields": _select_public_extension_params(
            params["stream_fields"],
            keys=("block_type", "message_id", "sequence"),
        ),
        "progress_fields": _select_public_extension_params(
            params["progress_fields"],
            keys=("type", "status"),
        ),
        "interrupt_fields": _select_public_extension_params(
            params["interrupt_fields"],
            keys=("request_id", "type", "phase"),
        ),
        "session_fields": _select_public_extension_params(
            params["session_fields"],
            keys=("id", "title"),
        ),
        "usage_fields": _select_public_extension_params(
            params["usage_fields"],
            keys=("input_tokens", "output_tokens", "total_tokens"),
        ),
    }


def _build_agent_card_description(
    settings: Settings,
    runtime_profile: RuntimeProfile,
    *,
    include_detailed_contracts: bool,
) -> str:
    base = (settings.a2a_description or "").strip() or "OpenCode A2A runtime."
    if not include_detailed_contracts:
        public_parts: list[str] = [
            base,
            (
                "Supports HTTP+JSON and JSON-RPC transports, streaming-first A2A messaging, "
                "and authenticated extended Agent Card discovery."
            ),
            (
                "Authenticated endpoints accept statically configured bearer/basic "
                "credentials, including multi-credential registry deployments."
            ),
            (
                "Single-tenant deployment; all consumers share the same underlying OpenCode "
                "workspace/environment."
            ),
        ]
        project = runtime_profile.runtime_context.as_dict().get("project")
        if isinstance(project, str) and project.strip():
            public_parts.append(f"Deployment project: {project}.")
        return " ".join(public_parts)

    summary = (
        "Supports HTTP+JSON and JSON-RPC transports, streaming-first A2A messaging "
        "(message/send, message/stream), authenticated extended Agent Card "
        "(agent/getAuthenticatedExtendedCard), task APIs (tasks/get, tasks/cancel, "
        "tasks/resubscribe; SDK-owned push notification config surfaces remain "
        "exposed but currently return unsupported; REST mappings include GET "
        "/v1/tasks and GET /v1/tasks/{id}:subscribe), shared "
        "session-binding/model-selection/streaming contracts, provider-private "
        "OpenCode session/provider/model/workspace-control/interrupt recovery "
        "extensions, and shared interrupt callback extensions."
    )
    parts: list[str] = [base, summary]
    parts.append(
        "Authenticated endpoints accept statically configured bearer/basic credentials, "
        "including multi-credential registry deployments."
    )
    parts.append(
        "This runtime profile is intended for single-tenant, self-hosted coding workflows."
    )
    parts.append(
        "Within one opencode-a2a instance, all consumers share the same "
        "underlying OpenCode workspace/environment; per-consumer workspace "
        "isolation is not provided."
    )
    runtime_context = runtime_profile.runtime_context.as_dict()
    project = runtime_context.get("project")
    if isinstance(project, str) and project.strip():
        parts.append(f"Deployment project: {project}.")
    workspace_root = runtime_context.get("workspace_root")
    if isinstance(workspace_root, str) and workspace_root.strip():
        parts.append(f"Workspace root: {workspace_root}.")
    return " ".join(parts)


def _build_chat_examples(project: str | None) -> list[str]:
    examples = [
        "Explain what this repository does.",
        "Summarize the API endpoints in this project.",
        "Review the attached diff and summarize the highest-risk findings.",
    ]
    if project:
        examples.append(f"Summarize current work items for project {project}.")
    return examples


def _build_session_management_skill_examples(
    *,
    capability_snapshot: JsonRpcCapabilitySnapshot,
) -> list[str]:
    examples = [
        "Get session status snapshots (method opencode.sessions.status).",
        "List OpenCode sessions with filters (method opencode.sessions.list).",
        "Get one OpenCode session (method opencode.sessions.get).",
        "List child sessions (method opencode.sessions.children).",
        "Read one session todo list (method opencode.sessions.todo).",
        "Read one session diff (method opencode.sessions.diff).",
        ("List messages with cursor pagination (method opencode.sessions.messages.list)."),
        "Get one session message (method opencode.sessions.messages.get).",
        (
            "Send async prompt to a session, including provider-private agent/subtask "
            "parts (method opencode.sessions.prompt_async)."
        ),
        "Send command to a session (method opencode.sessions.command).",
        "Fork a session at a message boundary (method opencode.sessions.fork).",
        "Share or unshare a session (methods opencode.sessions.share / opencode.sessions.unshare).",
        (
            "Summarize or undo a session (methods opencode.sessions.summarize / "
            "opencode.sessions.revert / opencode.sessions.unrevert)."
        ),
    ]
    if capability_snapshot.is_method_enabled(SESSION_METHODS["shell"]):
        examples.append("Run shell in a session (method opencode.sessions.shell).")
    return examples


def _build_interrupt_recovery_skill_examples() -> list[str]:
    return [
        "List pending permission interrupts (method opencode.permissions.list).",
        "List pending question interrupts (method opencode.questions.list).",
    ]


def _build_workspace_control_skill_examples(*, capability_snapshot) -> list[str]:  # noqa: ANN001
    examples = [
        "List OpenCode projects (method opencode.projects.list).",
        "List workspaces for the active project (method opencode.workspaces.list).",
    ]
    if capability_snapshot.is_method_enabled("opencode.worktrees.create"):
        examples.append("Create a worktree (method opencode.worktrees.create).")
    return examples


def _build_agent_extensions(
    *,
    settings: Settings,
    runtime_profile: RuntimeProfile,
    include_detailed_contracts: bool,
) -> list[AgentExtension]:
    session_binding_extension_params = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    model_selection_extension_params = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    streaming_extension_params = build_streaming_extension_params()
    session_management_extension_params = build_session_management_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    provider_discovery_extension_params = build_provider_discovery_extension_params(
        runtime_profile=runtime_profile,
    )
    workspace_control_extension_params = build_workspace_control_extension_params(
        runtime_profile=runtime_profile,
    )
    interrupt_recovery_extension_params = build_interrupt_recovery_extension_params(
        runtime_profile=runtime_profile,
    )
    interrupt_callback_extension_params = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    compatibility_profile_params = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
        supported_protocol_versions=settings.a2a_supported_protocol_versions,
        default_protocol_version=settings.a2a_protocol_version,
    )
    wire_contract_params = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
        supported_protocol_versions=settings.a2a_supported_protocol_versions,
        default_protocol_version=settings.a2a_protocol_version,
    )

    return [
        AgentExtension(
            uri=SESSION_BINDING_EXTENSION_URI,
            required=False,
            description=(
                "Shared contract to bind A2A messages to an existing upstream "
                "session when continuing a previous chat. Clients should pass "
                "metadata.shared.session.id. The metadata.opencode.directory field "
                "remains available as an OpenCode-private override under "
                "server-side directory boundary validation."
            ),
            params=(
                session_binding_extension_params
                if include_detailed_contracts
                else _select_public_extension_params(
                    session_binding_extension_params,
                    keys=(
                        "metadata_field",
                        "behavior",
                        "supported_metadata",
                        "provider_private_metadata",
                    ),
                )
            ),
        ),
        AgentExtension(
            uri=MODEL_SELECTION_EXTENSION_URI,
            required=False,
            description=(
                "Shared contract for request-scoped upstream model selection on the "
                "main chat path. Clients should pass metadata.shared.model with "
                "providerID/modelID."
            ),
            params=(
                model_selection_extension_params
                if include_detailed_contracts
                else _select_public_extension_params(
                    model_selection_extension_params,
                    keys=(
                        "metadata_field",
                        "behavior",
                        "applies_to_methods",
                        "supported_metadata",
                        "provider_private_metadata",
                        "fields",
                    ),
                )
            ),
        ),
        AgentExtension(
            uri=STREAMING_EXTENSION_URI,
            required=False,
            description=(
                "Shared streaming metadata contract for canonical block hints, "
                "timeline identity, usage, and interactive interrupt metadata."
            ),
            params=(
                streaming_extension_params
                if include_detailed_contracts
                else _build_public_streaming_extension_params(streaming_extension_params)
            ),
        ),
        AgentExtension(
            uri=SESSION_MANAGEMENT_EXTENSION_URI,
            required=False,
            description=(
                "Support OpenCode session read, mutation, and control methods through "
                "provider-private JSON-RPC extensions on the agent's A2A JSON-RPC "
                "interface."
            ),
            params=session_management_extension_params if include_detailed_contracts else None,
        ),
        AgentExtension(
            uri=PROVIDER_DISCOVERY_EXTENSION_URI,
            required=False,
            description=(
                "Expose OpenCode-specific provider/model discovery methods through "
                "JSON-RPC extensions."
            ),
            params=provider_discovery_extension_params if include_detailed_contracts else None,
        ),
        AgentExtension(
            uri=WORKSPACE_CONTROL_EXTENSION_URI,
            required=False,
            description=(
                "Expose OpenCode-specific project discovery plus workspace/worktree "
                "discovery and deployment-conditional control-plane mutations through "
                "JSON-RPC extensions. Workspace/worktree surfaces depend on upstream "
                "experimental endpoints."
            ),
            params=workspace_control_extension_params if include_detailed_contracts else None,
        ),
        AgentExtension(
            uri=INTERRUPT_RECOVERY_EXTENSION_URI,
            required=False,
            description=(
                "Expose adapter-local, identity-scoped interrupt recovery methods so "
                "clients can rediscover pending permission/question requests after "
                "reconnecting."
            ),
            params=interrupt_recovery_extension_params if include_detailed_contracts else None,
        ),
        AgentExtension(
            uri=INTERRUPT_CALLBACK_EXTENSION_URI,
            required=False,
            description=(
                "Handle interactive interrupt callbacks generated during "
                "streaming through shared JSON-RPC methods."
            ),
            params=(
                interrupt_callback_extension_params
                if include_detailed_contracts
                else _select_public_extension_params(
                    interrupt_callback_extension_params,
                    keys=("methods", "supported_interrupt_events", "request_id_field"),
                )
            ),
        ),
        AgentExtension(
            uri=COMPATIBILITY_PROFILE_EXTENSION_URI,
            required=False,
            description=(
                "Expose the A2A compatibility profile defining core baselines, "
                "extension retention policies, declared service behaviors, and "
                "deployment-conditional methods."
            ),
            params=compatibility_profile_params if include_detailed_contracts else None,
        ),
        AgentExtension(
            uri=WIRE_CONTRACT_EXTENSION_URI,
            required=False,
            description=(
                "Expose the wire-level contract declaring supported JSON-RPC methods, "
                "HTTP endpoints, declared service behaviors, and unified error "
                "contracts."
            ),
            params=wire_contract_params if include_detailed_contracts else None,
        ),
    ]


def _build_agent_skills(
    *,
    settings: Settings,
    capability_snapshot: JsonRpcCapabilitySnapshot,
    include_detailed_contracts: bool,
) -> list[AgentSkill]:
    if not include_detailed_contracts:
        return [
            AgentSkill(
                id="opencode.chat",
                name="OpenCode Chat",
                description=(
                    "Handle core A2A chat turns with shared session binding and optional "
                    "request-scoped model selection. Chat clients should continue accepting "
                    "text/plain responses; application/json is additive structured-output "
                    "support."
                ),
                input_modes=list(_CHAT_INPUT_MODES),
                output_modes=list(_CHAT_OUTPUT_MODES),
                tags=["assistant", "coding", "opencode", "core-a2a", "portable"],
            ),
            AgentSkill(
                id="opencode.sessions.management",
                name="OpenCode Session Management",
                description=(
                    "Read, mutate, and control OpenCode sessions through provider-private "
                    "JSON-RPC extensions."
                ),
                input_modes=list(_JSON_RPC_MODES),
                output_modes=list(_JSON_RPC_MODES),
                tags=["opencode", "sessions", "history", "provider-private"],
            ),
            AgentSkill(
                id="opencode.providers.query",
                name="OpenCode Provider Catalog",
                description=(
                    "Discover available upstream providers and models through provider-private "
                    "JSON-RPC extensions."
                ),
                input_modes=list(_JSON_RPC_MODES),
                output_modes=list(_JSON_RPC_MODES),
                tags=["opencode", "providers", "models", "provider-private"],
            ),
            AgentSkill(
                id="opencode.workspace.control",
                name="OpenCode Workspace Control",
                description=(
                    "Discover OpenCode projects, workspaces, and worktrees through "
                    "provider-private JSON-RPC extensions. Mutation methods are "
                    "deployment-conditional and workspace/worktree surfaces depend on "
                    "upstream experimental endpoints."
                ),
                input_modes=list(_JSON_RPC_MODES),
                output_modes=list(_JSON_RPC_MODES),
                tags=["opencode", "project", "workspace", "worktree", "provider-private"],
            ),
            AgentSkill(
                id="opencode.interrupt.recovery",
                name="OpenCode Interrupt Recovery",
                description=(
                    "Recover pending permission and question interrupts from the "
                    "adapter-local interrupt registry for the current caller."
                ),
                input_modes=list(_JSON_RPC_MODES),
                output_modes=list(_JSON_RPC_MODES),
                tags=["interrupt", "permission", "question", "provider-private"],
            ),
            AgentSkill(
                id="opencode.interrupt.callback",
                name="Shared Interrupt Callback",
                description=(
                    "Reply to streaming permission and question interrupts through shared "
                    "JSON-RPC callbacks."
                ),
                input_modes=list(_JSON_RPC_MODES),
                output_modes=list(_JSON_RPC_MODES),
                tags=["interrupt", "permission", "question", "shared"],
            ),
        ]

    return [
        AgentSkill(
            id="opencode.chat",
            name="OpenCode Chat",
            description=(
                "Handle core A2A message/send and message/stream requests by routing "
                "TextPart and FilePart inputs to OpenCode sessions with shared session "
                "binding and optional request-scoped model selection. Chat clients "
                "should continue accepting text/plain responses; application/json is "
                "additive structured-output support."
            ),
            input_modes=list(_CHAT_INPUT_MODES),
            output_modes=list(_CHAT_OUTPUT_MODES),
            tags=["assistant", "coding", "opencode", "core-a2a", "portable"],
            examples=_build_chat_examples(settings.a2a_project),
        ),
        AgentSkill(
            id="opencode.sessions.management",
            name="OpenCode Session Management",
            description=(
                "provider-private OpenCode session read/mutation/control surface exposed "
                "through JSON-RPC extensions."
            ),
            input_modes=list(_JSON_RPC_MODES),
            output_modes=list(_JSON_RPC_MODES),
            tags=["opencode", "sessions", "history", "provider-private"],
            examples=_build_session_management_skill_examples(
                capability_snapshot=capability_snapshot,
            ),
        ),
        AgentSkill(
            id="opencode.providers.query",
            name="OpenCode Provider Catalog",
            description=(
                "provider-private OpenCode provider/model discovery surface exposed "
                "through JSON-RPC extensions."
            ),
            input_modes=list(_JSON_RPC_MODES),
            output_modes=list(_JSON_RPC_MODES),
            tags=["opencode", "providers", "models", "provider-private"],
            examples=[
                "List available providers (method opencode.providers.list).",
                "List available models for a provider (method opencode.models.list).",
            ],
        ),
        AgentSkill(
            id="opencode.workspace.control",
            name="OpenCode Workspace Control",
            description=(
                "provider-private OpenCode project discovery plus workspace/worktree "
                "discovery surface with deployment-conditional mutation methods exposed "
                "through JSON-RPC extensions; workspace/worktree methods currently wrap "
                "upstream experimental endpoints."
            ),
            input_modes=list(_JSON_RPC_MODES),
            output_modes=list(_JSON_RPC_MODES),
            tags=["opencode", "project", "workspace", "worktree", "provider-private"],
            examples=_build_workspace_control_skill_examples(
                capability_snapshot=capability_snapshot,
            ),
        ),
        AgentSkill(
            id="opencode.interrupt.recovery",
            name="OpenCode Interrupt Recovery",
            description=(
                "adapter-local, identity-scoped interrupt recovery surface exposed "
                "through JSON-RPC extensions."
            ),
            input_modes=list(_JSON_RPC_MODES),
            output_modes=list(_JSON_RPC_MODES),
            tags=["interrupt", "permission", "question", "provider-private"],
            examples=_build_interrupt_recovery_skill_examples(),
        ),
        AgentSkill(
            id="opencode.interrupt.callback",
            name="Shared Interrupt Callback",
            description=(
                "Reply permission/question interrupts emitted during streaming via "
                "JSON-RPC methods a2a.interrupt.permission.reply, "
                "a2a.interrupt.question.reply, and a2a.interrupt.question.reject."
            ),
            input_modes=list(_JSON_RPC_MODES),
            output_modes=list(_JSON_RPC_MODES),
            tags=["interrupt", "permission", "question", "shared"],
            examples=[
                "Reply once/always/reject to a permission request by request_id.",
                "Submit answers for a question request by request_id.",
            ],
        ),
    ]


def _build_agent_card(
    settings: Settings,
    *,
    include_detailed_contracts: bool,
) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    runtime_profile = build_runtime_profile(settings)
    security_schemes: dict[str, SecurityScheme] = {}
    security_requirements: list[SecurityRequirement] = []
    if has_configured_auth_scheme(settings, "bearer"):
        security_schemes["bearerAuth"] = SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                description="Bearer token authentication",
                scheme="bearer",
                bearer_format="opaque",
            )
        )
        security_requirements.append(SecurityRequirement(schemes=cast(Any, {"bearerAuth": {}})))
    if has_configured_auth_scheme(settings, "basic"):
        security_schemes["basicAuth"] = SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                description="Basic authentication",
                scheme="basic",
            )
        )
        security_requirements.append(SecurityRequirement(schemes=cast(Any, {"basicAuth": {}})))
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)

    return AgentCard(
        name=settings.a2a_title,
        description=_build_agent_card_description(
            settings,
            runtime_profile,
            include_detailed_contracts=include_detailed_contracts,
        ),
        documentation_url=settings.a2a_documentation_url,
        version=settings.a2a_version,
        supported_interfaces=[
            AgentInterface(
                url=public_url,
                protocol_binding="HTTP+JSON",
                protocol_version=settings.a2a_protocol_version,
            ),
            AgentInterface(
                url=public_url,
                protocol_binding="JSONRPC",
                protocol_version=settings.a2a_protocol_version,
            ),
        ],
        default_input_modes=list(_CHAT_INPUT_MODES),
        default_output_modes=list(_CHAT_OUTPUT_MODES),
        capabilities=AgentCapabilities(
            streaming=True,
            extended_agent_card=True,
            extensions=_build_agent_extensions(
                settings=settings,
                runtime_profile=runtime_profile,
                include_detailed_contracts=include_detailed_contracts,
            ),
        ),
        skills=_build_agent_skills(
            settings=settings,
            capability_snapshot=capability_snapshot,
            include_detailed_contracts=include_detailed_contracts,
        ),
        security_schemes=security_schemes,
        security_requirements=security_requirements,
    )


def build_agent_card(settings: Settings) -> AgentCard:
    return _build_agent_card(settings, include_detailed_contracts=False)


def build_authenticated_extended_agent_card(settings: Settings) -> AgentCard:
    return _build_agent_card(settings, include_detailed_contracts=True)

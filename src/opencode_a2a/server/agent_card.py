from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
    TransportProtocol,
)

from ..config import Settings
from ..contracts.extensions import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_RECOVERY_EXTENSION_URI,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_EXTENSION_URI,
    SESSION_QUERY_METHODS,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    JsonRpcCapabilitySnapshot,
    build_capability_snapshot,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_interrupt_recovery_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_params,
)
from ..jsonrpc.application import SESSION_CONTEXT_PREFIX
from ..profile.runtime import RuntimeProfile, build_runtime_profile


def _build_agent_card_description(settings: Settings, runtime_profile: RuntimeProfile) -> str:
    base = (settings.a2a_description or "").strip() or "OpenCode A2A runtime."
    summary = (
        "Supports HTTP+JSON and JSON-RPC transports, streaming-first A2A messaging "
        "(message/send, message/stream), task APIs (tasks/get, tasks/cancel, "
        "tasks/resubscribe; REST mapping: GET /v1/tasks/{id}:subscribe), shared "
        "session-binding/model-selection/streaming contracts, provider-private "
        "OpenCode session/provider/model/interrupt recovery extensions, and "
        "shared interrupt callback extensions."
    )
    parts: list[str] = [base, summary]
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


def _build_session_query_skill_examples(
    *,
    capability_snapshot: JsonRpcCapabilitySnapshot,
) -> list[str]:
    examples = [
        "List OpenCode sessions (method opencode.sessions.list).",
        "List messages for a session (method opencode.sessions.messages.list).",
        "Send async prompt to a session (method opencode.sessions.prompt_async).",
        "Send command to a session (method opencode.sessions.command).",
    ]
    if capability_snapshot.is_method_enabled(SESSION_QUERY_METHODS["shell"]):
        examples.append("Run shell in a session (method opencode.sessions.shell).")
    return examples


def _build_interrupt_recovery_skill_examples() -> list[str]:
    return [
        "List pending permission interrupts (method opencode.permissions.list).",
        "List pending question interrupts (method opencode.questions.list).",
    ]


def build_agent_card(settings: Settings) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    base_url = public_url
    runtime_profile = build_runtime_profile(settings)
    security_schemes: dict[str, SecurityScheme] = {
        "bearerAuth": SecurityScheme(
            root=HTTPAuthSecurityScheme(
                description="Bearer token authentication",
                scheme="bearer",
                bearer_format="opaque",
            )
        )
    }
    security: list[dict[str, list[str]]] = [{"bearerAuth": []}]
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)

    session_binding_extension_params = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    model_selection_extension_params = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    streaming_extension_params = build_streaming_extension_params()
    session_query_extension_params = build_session_query_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    provider_discovery_extension_params = build_provider_discovery_extension_params(
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
    )
    wire_contract_params = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )

    return AgentCard(
        name=settings.a2a_title,
        description=_build_agent_card_description(settings, runtime_profile),
        url=base_url,
        documentation_url=settings.a2a_documentation_url,
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
        preferred_transport=TransportProtocol.http_json,
        default_input_modes=["text/plain", "application/octet-stream"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
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
                    params=session_binding_extension_params,
                ),
                AgentExtension(
                    uri=MODEL_SELECTION_EXTENSION_URI,
                    required=False,
                    description=(
                        "Shared contract for request-scoped upstream model selection on the "
                        "main chat path. Clients should pass metadata.shared.model with "
                        "providerID/modelID."
                    ),
                    params=model_selection_extension_params,
                ),
                AgentExtension(
                    uri=STREAMING_EXTENSION_URI,
                    required=False,
                    description=(
                        "Shared streaming metadata contract for canonical block hints, "
                        "timeline identity, usage, and interactive interrupt metadata."
                    ),
                    params=streaming_extension_params,
                ),
                AgentExtension(
                    uri=SESSION_QUERY_EXTENSION_URI,
                    required=False,
                    description=(
                        "Support OpenCode session list/history queries and async prompt injection "
                        "via custom JSON-RPC methods on the agent's A2A JSON-RPC interface."
                    ),
                    params=session_query_extension_params,
                ),
                AgentExtension(
                    uri=PROVIDER_DISCOVERY_EXTENSION_URI,
                    required=False,
                    description=(
                        "Expose OpenCode-specific provider/model discovery methods through "
                        "JSON-RPC extensions."
                    ),
                    params=provider_discovery_extension_params,
                ),
                AgentExtension(
                    uri=INTERRUPT_RECOVERY_EXTENSION_URI,
                    required=False,
                    description=(
                        "Expose provider-private interrupt recovery methods so clients can "
                        "list pending permission/question requests after reconnecting."
                    ),
                    params=interrupt_recovery_extension_params,
                ),
                AgentExtension(
                    uri=INTERRUPT_CALLBACK_EXTENSION_URI,
                    required=False,
                    description=(
                        "Handle interactive interrupt callbacks generated during "
                        "streaming through shared JSON-RPC methods."
                    ),
                    params=interrupt_callback_extension_params,
                ),
                AgentExtension(
                    uri=COMPATIBILITY_PROFILE_EXTENSION_URI,
                    required=False,
                    description=(
                        "Expose the A2A compatibility profile defining core baselines, "
                        "extension retention policies, declared service behaviors, and "
                        "deployment-conditional methods."
                    ),
                    params=compatibility_profile_params,
                ),
                AgentExtension(
                    uri=WIRE_CONTRACT_EXTENSION_URI,
                    required=False,
                    description=(
                        "Expose the wire-level contract declaring supported JSON-RPC methods, "
                        "HTTP endpoints, declared service behaviors, and unified error "
                        "contracts."
                    ),
                    params=wire_contract_params,
                ),
            ],
        ),
        skills=[
            AgentSkill(
                id="opencode.chat",
                name="OpenCode Chat",
                description=(
                    "Handle core A2A message/send and message/stream requests by routing "
                    "TextPart and FilePart inputs to OpenCode sessions with shared session "
                    "binding and optional request-scoped model selection."
                ),
                tags=["assistant", "coding", "opencode", "core-a2a", "portable"],
                examples=_build_chat_examples(settings.a2a_project),
            ),
            AgentSkill(
                id="opencode.sessions.query",
                name="OpenCode Sessions Query",
                description=(
                    "provider-private OpenCode session/history and session-control surface "
                    "exposed through JSON-RPC extensions."
                ),
                tags=["opencode", "sessions", "history", "provider-private"],
                examples=_build_session_query_skill_examples(
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
                tags=["opencode", "providers", "models", "provider-private"],
                examples=[
                    "List available providers (method opencode.providers.list).",
                    "List available models for a provider (method opencode.models.list).",
                ],
            ),
            AgentSkill(
                id="opencode.interrupt.recovery",
                name="OpenCode Interrupt Recovery",
                description=(
                    "provider-private OpenCode interrupt recovery surface exposed through "
                    "JSON-RPC extensions."
                ),
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
                tags=["interrupt", "permission", "question", "shared"],
                examples=[
                    "Reply once/always/reject to a permission request by request_id.",
                    "Submit answers for a question request by request_id.",
                ],
            ),
        ],
        additional_interfaces=[
            AgentInterface(transport=TransportProtocol.http_json, url=base_url),
            AgentInterface(transport=TransportProtocol.jsonrpc, url=base_url),
        ],
        security_schemes=security_schemes,
        security=security,
    )

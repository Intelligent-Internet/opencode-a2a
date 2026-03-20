from __future__ import annotations

import json
import logging
from typing import Any, cast

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
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings
from .extension_contracts import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_CALLBACK_METHODS,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_EXTENSION_URI,
    SESSION_QUERY_METHODS,
    STREAMING_EXTENSION_URI,
    WIRE_CONTRACT_EXTENSION_URI,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_params,
)
from .jsonrpc_ext import SESSION_CONTEXT_PREFIX
from .runtime_profile import RuntimeProfile, build_runtime_profile

logger = logging.getLogger(__name__)


def _parse_json_body(body_bytes: bytes) -> dict | None:
    try:
        payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _detect_sensitive_extension_method(payload: dict | None) -> str | None:
    if payload is None:
        return None
    method = payload.get("method")
    if not isinstance(method, str):
        return None
    sensitive_methods = set(SESSION_QUERY_METHODS.values()) | set(
        INTERRUPT_CALLBACK_METHODS.values()
    )
    if method in sensitive_methods:
        return method
    return None


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _normalize_content_type(value: str | None) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0].strip().lower()


def _is_json_content_type(content_type: str) -> bool:
    if not content_type:
        return False
    if content_type == "application/json":
        return True
    return content_type.endswith("+json")


def _decode_payload_preview(body: bytes, *, limit: int) -> str:
    if limit > 0 and len(body) > limit:
        preview = body[:limit].decode("utf-8", errors="replace")
        return f"{preview}...[truncated]"
    return body.decode("utf-8", errors="replace")


def _looks_like_jsonrpc_message_payload(payload: dict | None) -> bool:
    if payload is None:
        return False
    message = payload.get("message")
    if not isinstance(message, dict):
        return False
    if "parts" in message:
        return True
    role = message.get("role")
    return isinstance(role, str) and role in {"user", "agent"}


def _looks_like_jsonrpc_envelope(payload: dict | None) -> bool:
    if payload is None:
        return False
    method = payload.get("method")
    version = payload.get("jsonrpc")
    return isinstance(method, str) and isinstance(version, str)


class _RequestBodyTooLargeError(Exception):
    def __init__(self, *, limit: int, actual_size: int) -> None:
        super().__init__("Request body too large")
        self.limit = limit
        self.actual_size = actual_size


def _request_body_too_large_response(
    *,
    path: str,
    method: str,
    error: _RequestBodyTooLargeError,
) -> JSONResponse:
    logger.warning(
        "A2A request %s %s rejected: body_size=%s exceeds max_request_body_bytes=%s",
        method,
        path,
        error.actual_size,
        error.limit,
    )
    return JSONResponse(
        {"error": "Request body too large", "max_bytes": error.limit},
        status_code=413,
    )


def _build_agent_card_description(settings: Settings, runtime_profile: RuntimeProfile) -> str:
    base = (settings.a2a_description or "").strip() or "A2A wrapper service for OpenCode."
    summary = (
        "Supports HTTP+JSON and JSON-RPC transports, streaming-first A2A messaging "
        "(message/send, message/stream), task APIs (tasks/get, tasks/cancel, "
        "tasks/resubscribe; REST mapping: GET /v1/tasks/{id}:subscribe), shared "
        "session-binding/model-selection/streaming contracts, provider-private "
        "OpenCode session/provider/model extensions, and shared interrupt "
        "callback extensions."
    )
    parts: list[str] = [base, summary]
    parts.append("This server profile is intended for single-tenant, self-hosted coding workflows.")
    parts.append(
        "Within one opencode-a2a-server instance, all consumers share the same "
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


def _build_session_query_skill_examples(*, session_shell_enabled: bool) -> list[str]:
    examples = [
        "List OpenCode sessions (method opencode.sessions.list).",
        "List messages for a session (method opencode.sessions.messages.list).",
        "Send async prompt to a session (method opencode.sessions.prompt_async).",
        "Send command to a session (method opencode.sessions.command).",
    ]
    if session_shell_enabled:
        examples.append("Run shell in a session (method opencode.sessions.shell).")
    return examples


def _build_jsonrpc_extension_openapi_description(*, session_shell_enabled: bool) -> str:
    session_methods = [
        SESSION_QUERY_METHODS["list_sessions"],
        SESSION_QUERY_METHODS["get_session_messages"],
        SESSION_QUERY_METHODS["prompt_async"],
        SESSION_QUERY_METHODS["command"],
    ]
    if session_shell_enabled:
        session_methods.append(SESSION_QUERY_METHODS["shell"])
    provider_methods = ", ".join(sorted(PROVIDER_DISCOVERY_METHODS.values()))
    interrupt_methods = ", ".join(sorted(INTERRUPT_CALLBACK_METHODS.values()))
    return (
        "A2A JSON-RPC entrypoint. Supports core A2A methods "
        "(message/send, message/stream, tasks/get, tasks/cancel, tasks/resubscribe) "
        "plus shared model-selection metadata, OpenCode session/provider extensions, "
        "and shared interrupt callback methods.\n\n"
        f"OpenCode session query/control methods: {', '.join(session_methods)}.\n"
        f"OpenCode provider/model discovery methods: {provider_methods}.\n"
        f"Shared interrupt callback methods: {interrupt_methods}.\n\n"
        "Notification semantics: extension requests without JSON-RPC id return HTTP 204."
    )


def _build_jsonrpc_extension_openapi_examples(*, session_shell_enabled: bool) -> dict[str, Any]:
    examples = {
        "message_send": {
            "summary": "Send message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Explain what this repository does."}],
                    }
                },
            },
        },
        "message_stream": {
            "summary": "Stream message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "message/stream",
                "params": {
                    "message": {
                        "messageId": "msg-stream-1",
                        "role": "user",
                        "parts": [
                            {
                                "kind": "text",
                                "text": "Stream the answer and highlight key conclusions.",
                            }
                        ],
                    }
                },
            },
        },
        "message_send_model_override": {
            "summary": "Send message with shared model override",
            "value": {
                "jsonrpc": "2.0",
                "id": 103,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-model-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Answer with the faster model."}],
                    },
                    "metadata": {
                        "shared": {
                            "model": {
                                "providerID": "google",
                                "modelID": "gemini-2.5-flash",
                            }
                        }
                    },
                },
            },
        },
        "message_send_file_input": {
            "summary": "Send message with text + file input",
            "value": {
                "jsonrpc": "2.0",
                "id": 104,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-file-1",
                        "role": "user",
                        "parts": [
                            {
                                "kind": "text",
                                "text": "Review the attached file and summarize the main risks.",
                            },
                            {
                                "kind": "file",
                                "file": {
                                    "name": "report.pdf",
                                    "mimeType": "application/pdf",
                                    "uri": "file:///workspace/report.pdf",
                                },
                            },
                        ],
                    }
                },
            },
        },
        "session_list": {
            "summary": "List OpenCode sessions",
            "value": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": SESSION_QUERY_METHODS["list_sessions"],
                "params": {"limit": SESSION_QUERY_DEFAULT_LIMIT},
            },
        },
        "session_messages": {
            "summary": "List session messages",
            "value": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": SESSION_QUERY_METHODS["get_session_messages"],
                "params": {"session_id": "s-1", "limit": SESSION_QUERY_DEFAULT_LIMIT},
            },
        },
        "session_prompt_async": {
            "summary": "Send async prompt to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 21,
                "method": SESSION_QUERY_METHODS["prompt_async"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "Continue and summarize next steps."}]
                    },
                },
            },
        },
        "session_command": {
            "summary": "Send command to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 22,
                "method": SESSION_QUERY_METHODS["command"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "/review",
                        "arguments": "focus on security findings",
                    },
                },
            },
        },
        "providers_list": {
            "summary": "List available OpenCode providers",
            "value": {
                "jsonrpc": "2.0",
                "id": 24,
                "method": PROVIDER_DISCOVERY_METHODS["list_providers"],
                "params": {},
            },
        },
        "models_list": {
            "summary": "List available models for one provider",
            "value": {
                "jsonrpc": "2.0",
                "id": 25,
                "method": PROVIDER_DISCOVERY_METHODS["list_models"],
                "params": {"provider_id": "openai"},
            },
        },
        "permission_reply": {
            "summary": "Reply to permission interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 31,
                "method": INTERRUPT_CALLBACK_METHODS["reply_permission"],
                "params": {"request_id": "req-1", "reply": "once"},
            },
        },
        "question_reply": {
            "summary": "Reply to question interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 32,
                "method": INTERRUPT_CALLBACK_METHODS["reply_question"],
                "params": {"request_id": "req-2", "answers": [["answer"]]},
            },
        },
        "question_reject": {
            "summary": "Reject question interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 33,
                "method": INTERRUPT_CALLBACK_METHODS["reject_question"],
                "params": {"request_id": "req-3"},
            },
        },
    }
    if session_shell_enabled:
        examples["session_shell"] = {
            "summary": "Run shell command in an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 23,
                "method": SESSION_QUERY_METHODS["shell"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "agent": "code-reviewer",
                        "command": "git status --short",
                    },
                },
            },
        }
    return examples


def _build_rest_message_openapi_examples() -> dict[str, Any]:
    return {
        "basic_message": {
            "summary": "Send a basic user message (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Explain what this repository does."}],
                }
            },
        },
        "message_with_file_input": {
            "summary": "Send message with FilePart input (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-file-1",
                    "role": "ROLE_USER",
                    "content": [
                        {"text": "Review the attached file and summarize the main risks."},
                        {
                            "file": {
                                "name": "report.pdf",
                                "mimeType": "application/pdf",
                                "uri": "file:///workspace/report.pdf",
                            }
                        },
                    ],
                }
            },
        },
        "continue_session": {
            "summary": "Continue a historical OpenCode session",
            "value": {
                "message": {
                    "messageId": "msg-rest-continue-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Continue previous work and summarize next steps."}],
                },
                "metadata": {
                    "shared": {
                        "session": {"id": "s-1"},
                    }
                },
            },
        },
        "message_with_model_override": {
            "summary": "Send message with shared model override",
            "value": {
                "message": {
                    "messageId": "msg-rest-model-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Answer with the faster model."}],
                },
                "metadata": {
                    "shared": {
                        "model": {
                            "providerID": "google",
                            "modelID": "gemini-2.5-flash",
                        }
                    }
                },
            },
        },
    }


def _patch_jsonrpc_openapi_contract(
    app: FastAPI,
    settings: Settings,
    *,
    runtime_profile: RuntimeProfile,
) -> None:
    session_binding = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    model_selection = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    streaming = build_streaming_extension_params()
    session_query = build_session_query_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    provider_discovery = build_provider_discovery_extension_params(
        runtime_profile=runtime_profile,
    )
    interrupt_callback = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = original_openapi()
        paths = schema.get("paths")
        if isinstance(paths, dict):
            root_path = paths.get("/")
            if isinstance(root_path, dict):
                post = root_path.get("post")
                if isinstance(post, dict):
                    post["summary"] = "Handle A2A JSON-RPC Requests"
                    post["description"] = _build_jsonrpc_extension_openapi_description(
                        session_shell_enabled=runtime_profile.session_shell_enabled,
                    )
                    post["x-a2a-extension-contracts"] = {
                        "session_binding": session_binding,
                        "model_selection": model_selection,
                        "streaming": streaming,
                        "session_query": session_query,
                        "provider_discovery": provider_discovery,
                        "interrupt_callback": interrupt_callback,
                        "compatibility_profile": compatibility_profile,
                        "wire_contract": wire_contract,
                    }

                    request_body = post.setdefault("requestBody", {})
                    if isinstance(request_body, dict):
                        content = request_body.setdefault("content", {})
                        if isinstance(content, dict):
                            app_json = content.setdefault("application/json", {})
                            if isinstance(app_json, dict):
                                app_json["examples"] = _build_jsonrpc_extension_openapi_examples(
                                    session_shell_enabled=runtime_profile.session_shell_enabled,
                                )

            rest_post_contracts: dict[str, dict[str, Any]] = {
                "/v1/message:send": {
                    "summary": "Send Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON message send endpoint. "
                        "Use REST payload shape with message.content and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendMessageRequest",
                },
                "/v1/message:stream": {
                    "summary": "Stream Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON streaming endpoint. "
                        "Use REST payload shape with message.content and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendStreamingMessageRequest",
                },
            }
            rest_examples = _build_rest_message_openapi_examples()
            for rest_path, contract in rest_post_contracts.items():
                rest_path_item = paths.get(rest_path)
                if not isinstance(rest_path_item, dict):
                    continue
                rest_post = rest_path_item.get("post")
                if not isinstance(rest_post, dict):
                    continue

                rest_post["summary"] = contract["summary"]
                rest_post["description"] = contract["description"]
                request_body = rest_post.setdefault("requestBody", {})
                if not isinstance(request_body, dict):
                    continue
                request_body.setdefault("required", True)
                content = request_body.setdefault("content", {})
                if not isinstance(content, dict):
                    continue
                app_json = content.setdefault("application/json", {})
                if not isinstance(app_json, dict):
                    continue
                app_json["schema"] = {"$ref": contract["schema_ref"]}
                app_json["examples"] = rest_examples

        app.openapi_schema = schema
        return schema

    cast(Any, app).openapi = custom_openapi


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
                        "extension retention policies, and deployment-conditional methods."
                    ),
                    params=compatibility_profile_params,
                ),
                AgentExtension(
                    uri=WIRE_CONTRACT_EXTENSION_URI,
                    required=False,
                    description=(
                        "Expose the wire-level contract declaring supported JSON-RPC methods, "
                        "HTTP endpoints, and unified error contracts."
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
                    session_shell_enabled=settings.a2a_enable_session_shell
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


def _normalize_log_level(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        return normalized
    return "INFO"


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO))

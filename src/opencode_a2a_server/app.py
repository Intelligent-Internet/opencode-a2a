from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, cast

import uvicorn
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.events import EventConsumer
from a2a.server.request_handlers.default_request_handler import (
    TERMINAL_TASK_STATES,
    DefaultRequestHandler,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
    Task,
    TaskIdParams,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TransportProtocol,
)
from a2a.utils.errors import ServerError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from .agent import OpencodeAgentExecutor, _emit_metric
from .config import Settings
from .extension_contracts import (
    COMPATIBILITY_PROFILE_EXTENSION_URI,
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_CALLBACK_METHODS,
    MODEL_SELECTION_EXTENSION_URI,
    PROVIDER_DISCOVERY_EXTENSION_URI,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_BINDING_EXTENSION_URI,
    SESSION_CONTROL_METHODS,
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
    build_supported_jsonrpc_methods,
    build_wire_contract_params,
)
from .jsonrpc_ext import (
    SESSION_CONTEXT_PREFIX,
    OpencodeSessionQueryJSONRPCApplication,
)
from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)

_REQUEST_BODY_BYTES: ContextVar[bytes | None] = ContextVar(
    "_REQUEST_BODY_BYTES",
    default=None,
)

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext


class OpencodeRequestHandler(DefaultRequestHandler):
    """Custom request handler to gracefully handle client disconnects and prevent dead loops."""

    async def on_cancel_task(
        self,
        params: TaskIdParams,
        context=None,
    ) -> Task | None:
        task = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        # Idempotent contract:
        # repeated cancel on already-canceled task returns current terminal state.
        if task.status.state == TaskState.canceled:
            return task

        if task.status.state in TERMINAL_TASK_STATES:
            raise ServerError(
                error=TaskNotCancelableError(
                    message=f"Task cannot be canceled - current state: {task.status.state}"
                )
            )
        try:
            return await super().on_cancel_task(params, context)
        except ServerError as exc:
            # Race-safe idempotency: task may become canceled between pre-check and super call.
            if isinstance(exc.error, TaskNotCancelableError):
                refreshed = await self.task_store.get(params.id, context)
                if refreshed and refreshed.status.state == TaskState.canceled:
                    return refreshed
            raise

    async def on_resubscribe_to_task(
        self,
        params: TaskIdParams,
        context=None,
    ):
        task = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        # Subscribe contract: terminal tasks replay once and then close stream.
        if task.status.state in TERMINAL_TASK_STATES:
            yield task
            return

        async for event in super().on_resubscribe_to_task(params, context):
            yield event

    async def on_message_send_stream(self, params, context=None):
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)
        _emit_metric("a2a_stream_requests_total")
        _emit_metric("a2a_stream_active")
        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)
        stream_completed = False

        try:
            async for event in result_aggregator.consume_and_emit(consumer):
                if hasattr(event, "id") and event.id:
                    self._validate_task_id_match(task_id, event.id)
                await self._send_push_notification_if_needed(task_id, result_aggregator)
                yield event
            stream_completed = True
        except (asyncio.CancelledError, GeneratorExit):
            logger.warning("Client disconnected. Cancelling producer task %s", task_id)
            producer_task.cancel()
            await queue.close(immediate=True)
            raise
        finally:
            _emit_metric("a2a_stream_active", -1)
            logger.debug(
                "A2A stream request closed task_id=%s completed=%s",
                task_id,
                stream_completed,
            )
            cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
            cleanup_task.set_name(f"cleanup_producer:{task_id}")
            self._track_background_task(cleanup_task)

    async def on_message_send(self, params, context=None):
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)

        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        blocking = True
        if params.configuration and params.configuration.blocking is False:
            blocking = False

        interrupted_or_non_blocking = False
        try:

            async def push_notification_callback() -> None:
                await self._send_push_notification_if_needed(task_id, result_aggregator)

            (
                result,
                interrupted_or_non_blocking,
            ) = await result_aggregator.consume_and_break_on_interrupt(
                consumer,
                blocking=blocking,
                event_callback=push_notification_callback,
            )
        except Exception:
            logger.exception("Agent execution failed")
            raise
        finally:
            if interrupted_or_non_blocking:
                cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
                cleanup_task.set_name(f"cleanup_producer:{task_id}")
                self._track_background_task(cleanup_task)
            else:
                try:
                    if asyncio.current_task() and asyncio.current_task().cancelled():
                        logger.warning(
                            "Client disconnected from message request. Cancelling task %s", task_id
                        )
                        producer_task.cancel()
                        await queue.close(immediate=True)

                    await asyncio.shield(self._cleanup_producer(producer_task, task_id))
                except asyncio.CancelledError:
                    pass

        if not result:
            from a2a.types import InternalError
            from a2a.utils.errors import ServerError

            raise ServerError(error=InternalError())

        if hasattr(result, "id") and result.id:
            self._validate_task_id_match(task_id, result.id)
            if params.configuration:
                from a2a.utils.task import apply_history_length

                result = apply_history_length(result, params.configuration.history_length)

        await self._send_push_notification_if_needed(task_id, result_aggregator)

        return result


class IdentityAwareCallContextBuilder(DefaultCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        path = request.url.path
        raw_path = request.scope.get("raw_path")
        raw_value = ""
        if isinstance(raw_path, (bytes, bytearray)):
            raw_value = raw_path.decode(errors="ignore")
        is_stream = (
            path.endswith("/v1/message:stream")
            or path.endswith("/v1/message%3Astream")
            or raw_value.endswith("/v1/message:stream")
            or raw_value.endswith("/v1/message%3Astream")
        )
        if is_stream:
            context.state["a2a_streaming_request"] = True

        identity = getattr(request.state, "user_identity", None)
        if identity:
            context.state["identity"] = identity

        return context


def _build_deployment_context(settings: Settings) -> dict[str, str | bool]:
    context: dict[str, str | bool] = {
        "allow_directory_override": settings.a2a_allow_directory_override,
        "deployment_profile": "single_tenant_shared_workspace",
        "shared_workspace_across_consumers": True,
        "tenant_isolation": "none",
        "session_shell_enabled": settings.a2a_enable_session_shell,
    }
    if settings.a2a_project:
        context["project"] = settings.a2a_project
    if settings.opencode_directory:
        context["workspace_root"] = settings.opencode_directory
    if settings.opencode_provider_id:
        context["provider_id"] = settings.opencode_provider_id
    if settings.opencode_model_id:
        context["model_id"] = settings.opencode_model_id
    if settings.opencode_agent:
        context["agent"] = settings.opencode_agent
    if settings.opencode_variant:
        context["variant"] = settings.opencode_variant
    return context


def _build_agent_card_description(
    settings: Settings, deployment_context: dict[str, str | bool]
) -> str:
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
    project = deployment_context.get("project")
    if isinstance(project, str) and project.strip():
        parts.append(f"Deployment project: {project}.")
    workspace_root = deployment_context.get("workspace_root")
    if isinstance(workspace_root, str) and workspace_root.strip():
        parts.append(f"Workspace root: {workspace_root}.")
    provider_id = deployment_context.get("provider_id")
    model_id = deployment_context.get("model_id")
    if isinstance(provider_id, str) and isinstance(model_id, str):
        parts.append(f"Default upstream model: {provider_id}/{model_id}.")
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
        "plus OpenCode session/provider extensions and shared interrupt callback methods.\n\n"
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
                "params": {"limit": 20},
            },
        },
        "session_messages": {
            "summary": "List session messages",
            "value": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": SESSION_QUERY_METHODS["get_session_messages"],
                "params": {"session_id": "s-1", "limit": 20},
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
    deployment_context: dict[str, str | bool],
) -> None:
    session_binding = build_session_binding_extension_params(
        deployment_context=deployment_context,
        directory_override_enabled=bool(deployment_context["allow_directory_override"]),
    )
    model_selection = build_model_selection_extension_params(
        deployment_context=deployment_context,
    )
    streaming = build_streaming_extension_params()
    session_query = build_session_query_extension_params(
        deployment_context=deployment_context,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    provider_discovery = build_provider_discovery_extension_params(
        deployment_context=deployment_context,
    )
    interrupt_callback = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )
    compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )
    wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
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
                        session_shell_enabled=bool(deployment_context.get("session_shell_enabled")),
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
                                    session_shell_enabled=bool(
                                        deployment_context.get("session_shell_enabled")
                                    ),
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
    deployment_context = _build_deployment_context(settings)
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
        deployment_context=deployment_context,
        directory_override_enabled=settings.a2a_allow_directory_override,
    )
    model_selection_extension_params = build_model_selection_extension_params(
        deployment_context=deployment_context,
    )
    streaming_extension_params = build_streaming_extension_params()
    session_query_extension_params = build_session_query_extension_params(
        deployment_context=deployment_context,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    provider_discovery_extension_params = build_provider_discovery_extension_params(
        deployment_context=deployment_context,
    )
    interrupt_callback_extension_params = build_interrupt_callback_extension_params(
        deployment_context=deployment_context,
    )
    compatibility_profile_params = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )
    wire_contract_params = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        deployment_context=deployment_context,
    )

    return AgentCard(
        name=settings.a2a_title,
        description=_build_agent_card_description(settings, deployment_context),
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
                    "binding and shared model selection."
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


def add_auth_middleware(app: FastAPI, settings: Settings) -> None:
    token = settings.a2a_bearer_token

    def _unauthorized_response() -> JSONResponse:
        return JSONResponse(
            {"error": "Unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in {
            "/.well-known/agent-card.json",
            "/.well-known/agent.json",
        }:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return _unauthorized_response()
        provided = auth_header.split(" ", 1)[1].strip()
        if not secrets.compare_digest(provided, token):
            return _unauthorized_response()
        request.state.user_identity = f"bearer:{hashlib.sha256(provided.encode()).hexdigest()[:12]}"

        return await call_next(request)


def create_app(settings: Settings) -> FastAPI:
    client = OpencodeClient(settings)
    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=True,
        cancel_abort_timeout_seconds=settings.a2a_cancel_abort_timeout_seconds,
        session_cache_ttl_seconds=settings.a2a_session_cache_ttl_seconds,
        session_cache_maxsize=settings.a2a_session_cache_maxsize,
    )
    task_store = InMemoryTaskStore()
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await client.close()

    agent_card = build_agent_card(settings)
    context_builder = IdentityAwareCallContextBuilder()

    jsonrpc_methods = {
        **SESSION_QUERY_METHODS,
        **SESSION_CONTROL_METHODS,
        **PROVIDER_DISCOVERY_METHODS,
        **INTERRUPT_CALLBACK_METHODS,
    }
    if not settings.a2a_enable_session_shell:
        jsonrpc_methods.pop("shell", None)

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    app = OpencodeSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
        opencode_client=client,
        protocol_version=settings.a2a_protocol_version,
        supported_methods=build_supported_jsonrpc_methods(
            session_shell_enabled=settings.a2a_enable_session_shell,
        ),
        directory_resolver=executor.resolve_directory_for_control,
        session_claim=executor.claim_session_for_control,
        session_claim_finalize=executor.finalize_session_for_control,
        session_claim_release=executor.release_session_for_control,
        methods=jsonrpc_methods,
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)
    app.state.opencode_agent_executor = executor
    deployment_context = _build_deployment_context(settings)
    _patch_jsonrpc_openapi_contract(app, settings, deployment_context=deployment_context)

    rest_adapter = RESTAdapter(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
    )
    for route, callback in rest_adapter.routes().items():
        app.add_api_route(route[0], callback, methods=[route[1]])

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

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
        sensitive_methods = (
            set(SESSION_QUERY_METHODS.values())
            | set(PROVIDER_DISCOVERY_METHODS.values())
            | set(INTERRUPT_CALLBACK_METHODS.values())
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

    async def _get_request_body(request: Request) -> tuple[bytes, Token | None]:
        cached = _REQUEST_BODY_BYTES.get()
        if cached is not None:
            return cached, None

        limit = settings.a2a_max_request_body_bytes
        content_length = _parse_content_length(request.headers.get("content-length"))
        if limit > 0 and content_length is not None and content_length > limit:
            raise _RequestBodyTooLargeError(limit=limit, actual_size=content_length)

        if hasattr(request, "_body"):
            body = request._body
            if limit > 0 and len(body) > limit:
                raise _RequestBodyTooLargeError(limit=limit, actual_size=len(body))
        elif limit <= 0:
            body = await request.body()
        else:
            total = 0
            chunks: list[bytes] = []
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise _RequestBodyTooLargeError(limit=limit, actual_size=total)
                chunks.append(chunk)
            body = b"".join(chunks)
            request._body = body

        token = _REQUEST_BODY_BYTES.set(body)
        return body, token

    @app.middleware("http")
    async def enforce_request_body_limit(request: Request, call_next):
        token: Token | None = None
        limit = settings.a2a_max_request_body_bytes
        if limit <= 0 or request.method not in {"POST", "PUT", "PATCH"}:
            return await call_next(request)

        try:
            _, token = await _get_request_body(request)
            return await call_next(request)
        except _RequestBodyTooLargeError as error:
            return _request_body_too_large_response(
                path=request.url.path,
                method=request.method,
                error=error,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    @app.middleware("http")
    async def guard_rest_payload_shape(request: Request, call_next):
        token: Token | None = None
        if request.method != "POST" or request.url.path not in {
            "/v1/message:send",
            "/v1/message:stream",
        }:
            return await call_next(request)

        try:
            body, token = await _get_request_body(request)
            payload = _parse_json_body(body)
            if _looks_like_jsonrpc_envelope(payload) or _looks_like_jsonrpc_message_payload(
                payload
            ):
                return JSONResponse(
                    {
                        "error": (
                            "Invalid HTTP+JSON payload for REST endpoint. "
                            "Use message.content with ROLE_* role values, or call "
                            "POST / with method=message/send or method=message/stream."
                        )
                    },
                    status_code=400,
                )
            return await call_next(request)
        except _RequestBodyTooLargeError as error:
            return _request_body_too_large_response(
                path=request.url.path,
                method=request.method,
                error=error,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    @app.middleware("http")
    async def log_payloads(request: Request, call_next):
        token: Token | None = None
        if not settings.a2a_log_payloads:
            return await call_next(request)

        try:
            path = request.url.path
            limit = settings.a2a_log_body_limit
            content_type = _normalize_content_type(request.headers.get("content-type"))
            content_length = _parse_content_length(request.headers.get("content-length"))

            sensitive_method: str | None = None
            request_omit_reason: str | None = None

            if not _is_json_content_type(content_type):
                request_omit_reason = f"non-json content-type={content_type or 'unknown'}"
            elif limit > 0 and content_length is None:
                request_omit_reason = f"missing content-length with limit={limit}"
            elif limit > 0 and content_length is not None and content_length > limit:
                request_omit_reason = f"content-length={content_length} exceeds limit={limit}"
            else:
                body, token = await _get_request_body(request)
                # Detect session-query JSON-RPC methods regardless of deployment prefixes/root_path.
                payload = _parse_json_body(body)
                sensitive_method = _detect_sensitive_extension_method(payload)

                if sensitive_method:
                    logger.debug(
                        "A2A request %s %s method=%s",
                        request.method,
                        path,
                        sensitive_method,
                    )
                else:
                    logger.debug(
                        "A2A request %s %s body=%s",
                        request.method,
                        path,
                        _decode_payload_preview(body, limit=limit),
                    )

            if request_omit_reason:
                logger.debug(
                    "A2A request %s %s body=[omitted %s]",
                    request.method,
                    path,
                    request_omit_reason,
                )

            response = await call_next(request)
            if isinstance(response, StreamingResponse):
                if sensitive_method:
                    logger.debug("A2A response %s streaming method=%s", path, sensitive_method)
                else:
                    logger.debug("A2A response %s streaming", path)
                return response

            response_body = getattr(response, "body", b"") or b""
            if sensitive_method:
                logger.debug(
                    "A2A response %s status=%s bytes=%s method=%s",
                    path,
                    response.status_code,
                    len(response_body),
                    sensitive_method,
                )
                return response

            if request_omit_reason:
                logger.debug(
                    "A2A response %s status=%s bytes=%s body=[omitted request_%s]",
                    path,
                    response.status_code,
                    len(response_body),
                    request_omit_reason,
                )
                return response
            response_content_type = _normalize_content_type(response.headers.get("content-type"))
            if not _is_json_content_type(response_content_type):
                logger.debug(
                    "A2A response %s status=%s bytes=%s body=[omitted non-json content-type=%s]",
                    path,
                    response.status_code,
                    len(response_body),
                    response_content_type or "unknown",
                )
                return response

            logger.debug(
                "A2A response %s status=%s body=%s",
                path,
                response.status_code,
                _decode_payload_preview(response_body, limit=limit),
            )
            return response
        except _RequestBodyTooLargeError as error:
            return _request_body_too_large_response(
                path=request.url.path,
                method=request.method,
                error=error,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    add_auth_middleware(app, settings)

    return app


def _normalize_log_level(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        return normalized
    return "WARNING"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    log_level = _normalize_log_level(settings.a2a_log_level)
    _configure_logging(log_level)
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port, log_level=log_level.lower())


if __name__ == "__main__":
    main()

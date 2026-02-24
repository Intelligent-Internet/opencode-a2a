from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

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
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
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

from .agent import OpencodeAgentExecutor
from .config import Settings
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

SESSION_QUERY_METHODS = {
    "list_sessions": "opencode.sessions.list",
    "get_session_messages": "opencode.sessions.messages.list",
    "prompt_async": "opencode.sessions.prompt_async",
}
INTERRUPT_CALLBACK_METHODS = {
    "reply_permission": "opencode.permission.reply",
    "reply_question": "opencode.question.reply",
    "reject_question": "opencode.question.reject",
}

SESSION_BINDING_EXTENSION_URI = "urn:opencode-a2a:opencode-session-binding/v1"
SESSION_QUERY_EXTENSION_URI = "urn:opencode-a2a:opencode-session-query/v1"
INTERRUPT_CALLBACK_EXTENSION_URI = "urn:opencode-a2a:opencode-interrupt-callback/v1"


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
        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        try:
            async for event in result_aggregator.consume_and_emit(consumer):
                if hasattr(event, "id") and event.id:
                    self._validate_task_id_match(task_id, event.id)
                await self._send_push_notification_if_needed(task_id, result_aggregator)
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            logger.warning("Client disconnected. Cancelling producer task %s", task_id)
            producer_task.cancel()
            await queue.close(immediate=True)
            raise
        finally:
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
        "shared_workspace_across_consumers": True,
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
        "Supports HTTP+JSON and JSON-RPC transports, standard A2A messaging "
        "(message/send, message/stream), task APIs (tasks/get, tasks/cancel, "
        "tasks/resubscribe; REST mapping: GET /v1/tasks/{id}:subscribe), "
        "OpenCode session-query extensions, and interrupt callback extensions "
        "(permission/question reply)."
    )
    parts: list[str] = [base, summary]
    parts.append(
        "Within one opencode-a2a-serve instance, all consumers share the same "
        "underlying OpenCode workspace/environment."
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
    ]
    if project:
        examples.append(f"Summarize current work items for project {project}.")
    return examples


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

    if settings.a2a_oauth_authorization_url and settings.a2a_oauth_token_url:
        security_schemes["oauth2"] = SecurityScheme(
            root=OAuth2SecurityScheme(
                oauth2_metadata_url=settings.a2a_oauth_metadata_url,
                flows=OAuthFlows(
                    authorization_code=AuthorizationCodeOAuthFlow(
                        authorization_url=settings.a2a_oauth_authorization_url,
                        token_url=settings.a2a_oauth_token_url,
                        refresh_url=None,
                        scopes=settings.a2a_oauth_scopes,
                    )
                ),
            )
        )
        security.append({"oauth2": list(settings.a2a_oauth_scopes.keys())})

    return AgentCard(
        name=settings.a2a_title,
        description=_build_agent_card_description(settings, deployment_context),
        url=base_url,
        documentation_url=settings.a2a_documentation_url,
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
        preferred_transport=TransportProtocol.http_json,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=settings.a2a_streaming,
            extensions=[
                AgentExtension(
                    uri=SESSION_BINDING_EXTENSION_URI,
                    required=False,
                    description=(
                        "Contract to bind A2A messages to an existing OpenCode session "
                        "when continuing a previous chat. Clients should pass "
                        "metadata.opencode.session_id. The metadata.opencode.directory field is "
                        "also "
                        "supported under server-side directory boundary validation."
                    ),
                    params={
                        "metadata_namespace": "opencode",
                        "metadata_key": "opencode.session_id",
                        "behavior": "prefer_metadata_binding_else_create_session",
                        "supported_metadata": ["opencode.session_id", "opencode.directory"],
                        "directory_override_enabled": settings.a2a_allow_directory_override,
                        "shared_workspace_across_consumers": True,
                        "tenant_isolation": "none",
                        "deployment_context": deployment_context,
                        "notes": [
                            (
                                "If metadata.opencode.session_id is provided, the server will "
                                "send the message to that OpenCode session_id."
                            ),
                            (
                                "Otherwise, the server will create a new OpenCode session and "
                                "cache the (identity, contextId)->session_id mapping in memory "
                                "with TTL."
                            ),
                        ],
                    },
                ),
                AgentExtension(
                    uri=SESSION_QUERY_EXTENSION_URI,
                    required=False,
                    description=(
                        "Support OpenCode session list/history queries and async prompt injection "
                        "via custom JSON-RPC methods on the agent's A2A JSON-RPC interface."
                    ),
                    params={
                        "methods": SESSION_QUERY_METHODS,
                        "shared_workspace_across_consumers": True,
                        "tenant_isolation": "none",
                        "deployment_context": deployment_context,
                        "pagination": {
                            # Explicit, discoverable contract for generic clients.
                            "mode": "limit",
                            "behavior": "passthrough",
                            "params": ["limit"],
                            "applies_to": [
                                SESSION_QUERY_METHODS["list_sessions"],
                                SESSION_QUERY_METHODS["get_session_messages"],
                            ],
                        },
                        "method_contracts": {
                            SESSION_QUERY_METHODS["list_sessions"]: {
                                "params": {
                                    "optional": ["limit", "query.limit"],
                                    "unsupported": ["cursor", "page", "size"],
                                },
                                "result": {"fields": ["items"], "items_type": "Task[]"},
                            },
                            SESSION_QUERY_METHODS["get_session_messages"]: {
                                "params": {
                                    "required": ["session_id"],
                                    "optional": ["limit", "query.limit"],
                                    "unsupported": ["cursor", "page", "size"],
                                },
                                "result": {"fields": ["items"], "items_type": "Message[]"},
                            },
                            SESSION_QUERY_METHODS["prompt_async"]: {
                                "params": {
                                    "required": ["session_id", "request.parts"],
                                    "optional": [
                                        "request.messageID",
                                        "request.model",
                                        "request.agent",
                                        "request.noReply",
                                        "request.tools",
                                        "request.format",
                                        "request.system",
                                        "request.variant",
                                        "metadata.opencode.directory",
                                    ],
                                },
                                "result": {"fields": ["ok", "session_id"]},
                                "notification_response_status": 204,
                            },
                        },
                        "errors": {
                            # JSON-RPC standard errors still apply (e.g. -32602 invalid params).
                            # Business-level, server-defined errors (JSON-RPC reserved range).
                            "business_codes": {
                                "SESSION_NOT_FOUND": -32001,
                                "UPSTREAM_UNREACHABLE": -32002,
                                "UPSTREAM_HTTP_ERROR": -32003,
                                "UPSTREAM_PAYLOAD_ERROR": -32005,
                            },
                            # Stable fields returned in error.data for business errors.
                            "error_data_fields": [
                                "type",
                                "session_id",
                                "upstream_status",
                                "detail",
                            ],
                        },
                        # Result envelope varies by method:
                        # query methods return items[], prompt_async returns ack fields.
                        "result_envelope": {
                            "by_method": {
                                SESSION_QUERY_METHODS["list_sessions"]: {
                                    "fields": ["items"],
                                    "items_field": "items",
                                },
                                SESSION_QUERY_METHODS["get_session_messages"]: {
                                    "fields": ["items"],
                                    "items_field": "items",
                                },
                                SESSION_QUERY_METHODS["prompt_async"]: {
                                    "fields": ["ok", "session_id"],
                                },
                            }
                        },
                        "context_semantics": {
                            "a2a_context_id_field": "contextId",
                            "a2a_context_id_prefix": SESSION_CONTEXT_PREFIX,
                            "upstream_session_id_field": "metadata.opencode.session_id",
                        },
                    },
                ),
                AgentExtension(
                    uri=INTERRUPT_CALLBACK_EXTENSION_URI,
                    required=False,
                    description=(
                        "Handle interactive interrupt callbacks generated by OpenCode "
                        "(permission/question asked events) through JSON-RPC methods."
                    ),
                    params={
                        "methods": INTERRUPT_CALLBACK_METHODS,
                        "supported_interrupt_events": [
                            "permission.asked",
                            "question.asked",
                        ],
                        "permission_reply_values": ["once", "always", "reject"],
                        "question_reply_contract": {
                            "answers": "array of answer arrays (same order as asked questions)"
                        },
                        "metadata_namespace": "opencode",
                        "supported_metadata": ["opencode.directory"],
                        "context_fields": {
                            "directory": "metadata.opencode.directory",
                        },
                        "success_result_fields": ["ok", "request_id"],
                        "errors": {
                            "business_codes": {
                                "INTERRUPT_REQUEST_NOT_FOUND": -32004,
                                "UPSTREAM_UNREACHABLE": -32002,
                                "UPSTREAM_HTTP_ERROR": -32003,
                            },
                            "error_types": [
                                "INTERRUPT_REQUEST_NOT_FOUND",
                                "INTERRUPT_REQUEST_EXPIRED",
                                "INTERRUPT_TYPE_MISMATCH",
                            ],
                            "error_data_fields": ["type", "request_id", "upstream_status"],
                        },
                        "shared_workspace_across_consumers": True,
                        "tenant_isolation": "none",
                        "deployment_context": deployment_context,
                    },
                ),
            ],
        ),
        skills=[
            AgentSkill(
                id="opencode.chat",
                name="OpenCode Chat",
                description=(
                    "Handle message/send and message/stream requests by routing user text to "
                    "OpenCode sessions."
                ),
                tags=["assistant", "coding", "opencode"],
                examples=_build_chat_examples(settings.a2a_project),
            ),
            AgentSkill(
                id="opencode.sessions.query",
                name="OpenCode Sessions Query",
                description=(
                    "Query OpenCode sessions/histories and submit async prompts via JSON-RPC "
                    "extension methods opencode.sessions.list, "
                    "opencode.sessions.messages.list, and opencode.sessions.prompt_async."
                ),
                tags=["opencode", "sessions", "history"],
                examples=[
                    "List OpenCode sessions (method opencode.sessions.list).",
                    "List messages for a session (method opencode.sessions.messages.list).",
                    "Send async prompt to a session (method opencode.sessions.prompt_async).",
                ],
            ),
            AgentSkill(
                id="opencode.interrupt.callback",
                name="OpenCode Interrupt Callback",
                description=(
                    "Reply permission/question interrupts emitted during streaming via "
                    "JSON-RPC methods opencode.permission.reply, "
                    "opencode.question.reply, and opencode.question.reject."
                ),
                tags=["opencode", "interrupt", "permission", "question"],
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
        streaming_enabled=settings.a2a_streaming,
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

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    app = OpencodeSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
        opencode_client=client,
        methods={
            **SESSION_QUERY_METHODS,
            **INTERRUPT_CALLBACK_METHODS,
        },
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)

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

    def _detect_opencode_extension_method(payload: dict | None) -> str | None:
        if payload is None:
            return None
        method = payload.get("method")
        if not isinstance(method, str):
            return None
        if method.startswith("opencode."):
            return method
        return None

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

    async def _get_request_body(request: Request) -> tuple[bytes, Token | None]:
        cached = _REQUEST_BODY_BYTES.get()
        if cached is not None:
            return cached, None
        body = await request.body()
        token = _REQUEST_BODY_BYTES.set(body)
        return body, token

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
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    @app.middleware("http")
    async def log_payloads(request: Request, call_next):
        token: Token | None = None
        if not settings.a2a_log_payloads:
            return await call_next(request)

        try:
            body, token = await _get_request_body(request)
            path = request.url.path
            # Detect session-query JSON-RPC methods regardless of deployment prefixes/root_path.
            payload = _parse_json_body(body)
            sensitive_method = _detect_opencode_extension_method(payload)

            if sensitive_method:
                logger.debug(
                    "A2A request %s %s method=%s",
                    request.method,
                    path,
                    sensitive_method,
                )
                response = await call_next(request)
                if isinstance(response, StreamingResponse):
                    logger.debug("A2A response %s streaming method=%s", path, sensitive_method)
                    return response
                response_body = getattr(response, "body", b"") or b""
                logger.debug(
                    "A2A response %s status=%s bytes=%s method=%s",
                    path,
                    response.status_code,
                    len(response_body),
                    sensitive_method,
                )
                return response

            body_text = body.decode("utf-8", errors="replace")
            limit = settings.a2a_log_body_limit
            if limit > 0 and len(body_text) > limit:
                body_text = f"{body_text[:limit]}...[truncated]"
            logger.debug(
                "A2A request %s %s body=%s",
                request.method,
                request.url.path,
                body_text,
            )

            response = await call_next(request)
            if isinstance(response, StreamingResponse):
                logger.debug("A2A response %s streaming", request.url.path)
                return response

            response_body = getattr(response, "body", b"") or b""
            resp_text = response_body.decode("utf-8", errors="replace")
            if limit > 0 and len(resp_text) > limit:
                resp_text = f"{resp_text[:limit]}...[truncated]"
            logger.debug(
                "A2A response %s status=%s body=%s",
                request.url.path,
                response.status_code,
                resp_text,
            )
            return response
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    add_auth_middleware(app, settings)

    return app


def _normalize_log_level(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        return normalized
    return "INFO"


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

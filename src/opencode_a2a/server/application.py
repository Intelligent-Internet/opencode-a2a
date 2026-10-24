from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import uvicorn
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.rest_dispatcher import RestDispatcher
from a2a.types import (
    InvalidRequestError,
    SendMessageRequest,
    SubscribeToTaskRequest,
)
from a2a.utils.errors import (
    A2A_REST_ERROR_MAPPING,
    A2AError,
    ErrorMapping,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from google.protobuf.json_format import ParseDict, ParseError
from pydantic_settings import BaseSettings
from starlette.middleware.gzip import GZipMiddleware

from ..a2a_protocol import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)
from ..config import Settings
from ..contracts.extensions import (
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_METHODS,
    PROVIDER_DISCOVERY_METHODS,
    build_capability_snapshot,
)
from ..execution.executor import OpencodeAgentExecutor
from ..execution.metrics import render_prometheus_metrics
from ..jsonrpc.application import (
    OpencodeSessionManagementJSONRPCApplication,
)
from ..jsonrpc.error_responses import build_http_error_body
from ..opencode_upstream_client import OpencodeUpstreamClient
from ..profile.runtime import build_runtime_profile
from ..protocol_versions import A2A_PROTOCOL_VERSION
from ..trace_context import install_log_record_factory
from .agent_card import (
    build_agent_card,
)
from .client_manager import A2AClientManager
from .context_helpers import AuthenticatedIdentityUser
from .database import build_database_engine
from .lifespan import build_lifespan
from .middleware import (
    build_agent_card_etag,
    install_runtime_middlewares,
)
from .openapi import (
    _patch_jsonrpc_openapi_contract,
)
from .request_handler import OpencodeRequestHandler
from .request_parsing import (
    _parse_json_body,
    validate_send_message_request,
)
from .rest_responses import A2AJSONResponse
from .runtime_limits import StreamBudgetExceeded, apply_stream_budget
from .state_store import (
    build_runtime_state_runtime,
)
from .task_store import (
    build_task_store_runtime,
    describe_lightweight_persistence_backend,
)

logger = logging.getLogger(__name__)
TASK_STORE_ERROR_TYPE = "TASK_STORE_UNAVAILABLE"
PUSH_NOTIFICATIONS_UNSUPPORTED_MESSAGE = "Push notifications are not supported by the agent"


def _rest_error_response(
    *,
    request: Request,
    error: Exception,
) -> JSONResponse:
    del request
    logger_fn = logger.exception
    logger_message = "Unexpected REST message route failure"

    if isinstance(error, A2AError):
        mapping = A2A_REST_ERROR_MAPPING.get(
            type(error),
            ErrorMapping(500, "INTERNAL", "INTERNAL_ERROR"),
        )
        message = getattr(error, "message", str(error))
        metadata = getattr(error, "data", None) or {}
        logger_fn = logger.error if mapping.http_code >= 500 else logger.warning
        logger_message = (
            f"REST message route failed status={mapping.http_code} "
            f"reason={mapping.reason} message={message}"
        )
        logger_fn(logger_message)
        return JSONResponse(
            build_http_error_body(
                status_code=mapping.http_code,
                status=mapping.grpc_status,
                message=message,
                reason=mapping.reason,
                metadata=metadata,
            ),
            status_code=mapping.http_code,
        )

    if isinstance(error, StreamBudgetExceeded):
        logger.warning(
            "REST stream rejected before SSE start: %s",
            error,
        )
        return JSONResponse(
            build_http_error_body(
                status_code=429,
                status="RESOURCE_EXHAUSTED",
                message=str(error),
                reason="STREAM_BUDGET_EXCEEDED",
            ),
            status_code=429,
        )

    if isinstance(error, ParseError):
        message = str(error)
        logger_fn = logger.warning
        logger_message = f"REST message payload parse error: {message}"
        logger_fn(logger_message)
        return JSONResponse(
            build_http_error_body(
                status_code=400,
                status="INVALID_ARGUMENT",
                message=message,
                reason="INVALID_REQUEST",
            ),
            status_code=400,
        )

    logger_fn(logger_message)
    return JSONResponse(
        build_http_error_body(
            status_code=500,
            status="INTERNAL",
            message="unknown exception",
            reason="INTERNAL_ERROR",
        ),
        status_code=500,
    )


def _parse_rest_send_message_request(body: bytes):
    payload = _parse_json_body(body)
    if payload is None:
        raise InvalidRequestError(message="REST message payload must be a JSON object.")
    message = payload.get("message")
    if isinstance(message, dict):
        if "content" in message:
            raise InvalidRequestError(
                message="REST message payload must use message.parts, not message.content."
            )
        role = message.get("role")
        if isinstance(role, str) and role in {"user", "agent"}:
            raise InvalidRequestError(
                message="REST message payload must use ROLE_* values for message.role."
            )
        parts = message.get("parts")
        if isinstance(parts, list):
            for index, part in enumerate(parts):
                if isinstance(part, dict) and ("kind" in part or "type" in part or "file" in part):
                    raise InvalidRequestError(
                        message=(
                            f"message.parts[{index}] must use direct Part fields "
                            "such as text, raw, url, or data."
                        )
                    )
    request = ParseDict(
        payload,
        SendMessageRequest(),
        ignore_unknown_fields=True,
    )
    try:
        validate_send_message_request(request)
    except ValueError as exc:
        raise InvalidRequestError(message=str(exc)) from exc
    return request


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from a2a.server.context import ServerCallContext


class IdentityAwareCallContextBuilder(DefaultServerCallContextBuilder):
    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        headers = {key: value for key, value in request.headers.items()}
        headers["A2A-Version"] = getattr(request.state, "a2a_protocol_version", "1.0")
        context.state["headers"] = headers
        path = request.url.path
        raw_path = request.scope.get("raw_path")
        raw_value = ""
        if isinstance(raw_path, (bytes, bytearray)):
            raw_value = raw_path.decode(errors="ignore")
        is_stream = (
            path.endswith("/message:stream")
            or path.endswith("/message%3Astream")
            or raw_value.endswith("/message:stream")
            or raw_value.endswith("/message%3Astream")
        )
        if is_stream:
            context.state["a2a_streaming_request"] = True

        identity = getattr(request.state, "user_identity", None)
        if identity:
            context.state["identity"] = identity
            context.user = AuthenticatedIdentityUser(identity)
        auth_scheme = getattr(request.state, "user_auth_scheme", None)
        if auth_scheme:
            context.state["auth_scheme"] = auth_scheme
        credential_id = getattr(request.state, "user_credential_id", None)
        if credential_id:
            context.state["credential_id"] = credential_id
        traceparent = getattr(request.state, "traceparent", None)
        if traceparent:
            context.state["traceparent"] = traceparent
        tracestate = getattr(request.state, "tracestate", None)
        if tracestate:
            context.state["tracestate"] = tracestate
        trace_id = getattr(request.state, "trace_id", None)
        if trace_id:
            context.state["trace_id"] = trace_id

        return context


class StreamBudgetedRequestHandler:
    """Apply repository stream budgets through the public RequestHandler API."""

    def __init__(
        self,
        request_handler: RequestHandler,
        *,
        stream_budget_max_bytes: int,
        stream_budget_max_duration_seconds: float,
        stream_budget_idle_timeout_seconds: float,
    ) -> None:
        self._request_handler = request_handler
        self._stream_budget_max_bytes = stream_budget_max_bytes
        self._stream_budget_max_duration_seconds = stream_budget_max_duration_seconds
        self._stream_budget_idle_timeout_seconds = stream_budget_idle_timeout_seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request_handler, name)

    def _apply_budget(
        self,
        stream: AsyncIterator[Any],
    ) -> AsyncIterator[Any]:
        return apply_stream_budget(
            stream,
            max_bytes=self._stream_budget_max_bytes,
            max_duration_seconds=self._stream_budget_max_duration_seconds,
            idle_timeout_seconds=self._stream_budget_idle_timeout_seconds,
        )

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncIterator[Any]:
        async for item in self._apply_budget(
            self._request_handler.on_message_send_stream(params, context)
        ):
            yield item

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncIterator[Any]:
        async for item in self._apply_budget(
            self._request_handler.on_subscribe_to_task(params, context)
        ):
            yield item


def create_app(settings: Settings) -> FastAPI:
    install_log_record_factory()
    database_engine = (
        build_database_engine(settings) if settings.a2a_task_store_backend == "database" else None
    )
    runtime_state_runtime = build_runtime_state_runtime(
        settings,
        engine=database_engine,
    )
    upstream_client = OpencodeUpstreamClient(
        settings,
        interrupt_request_repository=runtime_state_runtime.interrupt_request_repository,
    )
    client_manager = A2AClientManager(settings)
    agent_card = build_agent_card(settings)
    extended_agent_card = build_agent_card(settings, include_detailed_contracts=True)
    executor = OpencodeAgentExecutor(
        upstream_client,
        streaming_enabled=True,
        cancel_abort_timeout_seconds=settings.a2a_cancel_abort_timeout_seconds,
        pending_session_claim_ttl_seconds=settings.a2a_pending_session_claim_ttl_seconds,
        a2a_client_manager=client_manager,
        session_state_repository=runtime_state_runtime.session_state_repository,
    )
    task_store_runtime = build_task_store_runtime(
        settings,
        engine=database_engine,
    )
    task_store = task_store_runtime.task_store
    handler = OpencodeRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
        extended_agent_card=extended_agent_card,
        event_stream=task_store_runtime.event_stream,
        output_modes=task_store_runtime.output_modes,
    )

    context_builder = IdentityAwareCallContextBuilder()
    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)

    jsonrpc_methods = {
        **capability_snapshot.session_management_methods(),
        **dict(PROVIDER_DISCOVERY_METHODS),
        **capability_snapshot.workspace_control_methods(),
        **dict(INTERRUPT_RECOVERY_METHODS),
        **dict(INTERRUPT_CALLBACK_METHODS),
    }

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    jsonrpc_app = OpencodeSessionManagementJSONRPCApplication(
        http_handler=handler,
        context_builder=context_builder,
        upstream_client=upstream_client,
        supported_methods=capability_snapshot.supported_jsonrpc_methods(),
        directory_resolver=(
            partial(
                executor._sandbox_policy.resolve_directory,
                default_directory=upstream_client.directory,
            )
            if hasattr(executor, "_sandbox_policy")
            else None
        ),
        session_claim=getattr(executor._session_manager, "claim_preferred_session", None),
        session_claim_finalize=getattr(executor._session_manager, "finalize_session_claim", None),
        session_claim_release=getattr(
            executor._session_manager,
            "release_preferred_session_claim",
            None,
        ),
        stream_budget_max_bytes=settings.a2a_stream_max_bytes,
        stream_budget_max_duration_seconds=settings.a2a_stream_max_duration_seconds,
        stream_budget_idle_timeout_seconds=settings.a2a_stream_idle_timeout_seconds,
        methods=jsonrpc_methods,
    )
    rest_request_handler = cast(
        RequestHandler,
        StreamBudgetedRequestHandler(
            handler,
            stream_budget_max_bytes=settings.a2a_stream_max_bytes,
            stream_budget_max_duration_seconds=settings.a2a_stream_max_duration_seconds,
            stream_budget_idle_timeout_seconds=settings.a2a_stream_idle_timeout_seconds,
        ),
    )
    rest_dispatcher = RestDispatcher(
        request_handler=rest_request_handler,
        context_builder=context_builder,
    )
    public_card_etag = build_agent_card_etag(agent_card)
    extended_card_etag = build_agent_card_etag(extended_agent_card)
    persistence_summary = describe_lightweight_persistence_backend(settings)
    lifespan = build_lifespan(
        request_handler=handler,
        database_engine=database_engine,
        task_store_runtime=task_store_runtime,
        runtime_state_runtime=runtime_state_runtime,
        client_manager=client_manager,
        upstream_client=upstream_client,
        persistence_summary=persistence_summary,
    )

    app = FastAPI(
        title=settings.a2a_title,
        version=settings.a2a_version,
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=settings.a2a_http_gzip_minimum_size)
    jsonrpc_app.add_routes_to_app(app)

    async def public_agent_card_route() -> JSONResponse:
        return JSONResponse(agent_card_to_dict(agent_card))

    async def authenticated_extended_agent_card_route() -> JSONResponse:
        return JSONResponse(agent_card_to_dict(extended_agent_card))

    app.add_api_route(AGENT_CARD_WELL_KNOWN_PATH, public_agent_card_route, methods=["GET"])
    app.add_api_route(
        "/message:send",
        rest_dispatcher.on_message_send,
        methods=["POST"],
        response_class=A2AJSONResponse,
    )
    app.add_api_route("/message:stream", rest_dispatcher.on_message_send_stream, methods=["POST"])
    app.add_api_route(
        "/tasks/{id}:cancel",
        rest_dispatcher.on_cancel_task,
        methods=["POST"],
        response_class=A2AJSONResponse,
    )
    app.add_api_route(
        "/tasks/{id}:subscribe",
        rest_dispatcher.on_subscribe_to_task,
        methods=["GET"],
        operation_id="subscribe_to_task_get",
    )
    app.add_api_route(
        "/tasks/{id}:subscribe",
        rest_dispatcher.on_subscribe_to_task,
        methods=["POST"],
        operation_id="subscribe_to_task_post",
    )
    app.add_api_route(
        "/tasks/{id}",
        rest_dispatcher.on_get_task,
        methods=["GET"],
        response_class=A2AJSONResponse,
    )

    app.add_api_route(
        "/tasks/{id}/pushNotificationConfigs/{push_id}",
        rest_dispatcher.get_push_notification,
        methods=["GET"],
    )
    app.add_api_route(
        "/tasks/{id}/pushNotificationConfigs/{push_id}",
        rest_dispatcher.delete_push_notification,
        methods=["DELETE"],
    )
    app.add_api_route(
        "/tasks/{id}/pushNotificationConfigs",
        rest_dispatcher.set_push_notification,
        methods=["POST"],
    )
    app.add_api_route(
        "/tasks/{id}/pushNotificationConfigs",
        rest_dispatcher.list_push_notifications,
        methods=["GET"],
    )
    app.add_api_route(
        "/tasks",
        rest_dispatcher.list_tasks,
        methods=["GET"],
        response_class=A2AJSONResponse,
    )
    app.add_api_route(
        EXTENDED_AGENT_CARD_PATH,
        authenticated_extended_agent_card_route,
        methods=["GET"],
    )
    app.state._jsonrpc_app = jsonrpc_app
    app.state.task_store = task_store
    app.state.persistence_summary = persistence_summary
    app.state.agent_executor = executor
    app.state.upstream_client = upstream_client
    app.state.a2a_client_manager = client_manager
    _patch_jsonrpc_openapi_contract(app, settings, runtime_profile=runtime_profile)
    install_runtime_middlewares(
        app,
        settings,
        public_card_etag=public_card_etag,
        extended_card_etag=extended_card_etag,
    )

    @app.get("/health")
    async def health_check():
        return runtime_profile.health_payload(
            service="opencode-a2a",
            version=settings.a2a_version,
            protocol_version=A2A_PROTOCOL_VERSION,
        )

    if settings.a2a_metrics_enabled:

        @app.get("/metrics", response_class=PlainTextResponse)
        async def metrics_check() -> PlainTextResponse:
            return PlainTextResponse(
                render_prometheus_metrics(),
                media_type="text/plain; version=0.0.4",
            )

    return app


def _normalize_log_level(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        return normalized
    return "WARNING"


def _configure_logging(level: str) -> None:
    install_log_record_factory()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s [trace_id=%(trace_id)s]: %(message)s",
    )
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def main() -> None:
    settings_cls: type[BaseSettings] = Settings
    settings = cast(Settings, settings_cls())
    app = create_app(settings)
    log_level = _normalize_log_level(settings.a2a_log_level)
    _configure_logging(log_level)
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port, log_level=log_level.lower())


if __name__ == "__main__":
    main()

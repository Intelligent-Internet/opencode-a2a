from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

import uvicorn
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.apps.rest.rest_adapter import (
    EventSourceResponse,
    InvalidRequestError,
    RESTAdapter,
    rest_stream_error_handler,
)
from a2a.server.events import EventConsumer
from a2a.server.request_handlers.default_request_handler import (
    TERMINAL_TASK_STATES,
    DefaultRequestHandler,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    Task,
    TaskIdParams,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
)
from a2a.utils.errors import ServerError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from ..config import Settings
from ..contracts.extensions import (
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
    build_capability_snapshot,
)
from ..execution.executor import OpencodeAgentExecutor, _emit_metric
from ..jsonrpc.application import (
    OpencodeSessionQueryJSONRPCApplication,
)
from ..opencode_upstream_client import OpencodeUpstreamClient
from ..profile.runtime import build_runtime_profile
from .agent_card import (
    _build_agent_card_description,
    _build_chat_examples,
    _build_session_query_skill_examples,
    build_agent_card,
)
from .openapi import (
    _build_jsonrpc_extension_openapi_description,
    _build_jsonrpc_extension_openapi_examples,
    _build_rest_message_openapi_examples,
    _patch_jsonrpc_openapi_contract,
)
from .request_parsing import (
    _decode_payload_preview,
    _detect_sensitive_extension_method,
    _is_json_content_type,
    _looks_like_jsonrpc_envelope,
    _looks_like_jsonrpc_message_payload,
    _normalize_content_type,
    _parse_content_length,
    _parse_json_body,
    _request_body_too_large_response,
    _RequestBodyTooLargeError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_RequestBodyTooLargeError",
    "COMPATIBILITY_PROFILE_EXTENSION_URI",
    "INTERRUPT_CALLBACK_EXTENSION_URI",
    "INTERRUPT_CALLBACK_METHODS",
    "MODEL_SELECTION_EXTENSION_URI",
    "PROVIDER_DISCOVERY_EXTENSION_URI",
    "PROVIDER_DISCOVERY_METHODS",
    "SESSION_BINDING_EXTENSION_URI",
    "SESSION_CONTROL_METHODS",
    "SESSION_QUERY_EXTENSION_URI",
    "SESSION_QUERY_METHODS",
    "STREAMING_EXTENSION_URI",
    "WIRE_CONTRACT_EXTENSION_URI",
    "_build_agent_card_description",
    "_build_chat_examples",
    "_build_jsonrpc_extension_openapi_description",
    "_build_jsonrpc_extension_openapi_examples",
    "_build_rest_message_openapi_examples",
    "_build_session_query_skill_examples",
    "_configure_logging",
    "_decode_payload_preview",
    "_detect_sensitive_extension_method",
    "_is_json_content_type",
    "_looks_like_jsonrpc_envelope",
    "_looks_like_jsonrpc_message_payload",
    "_normalize_content_type",
    "_normalize_log_level",
    "_parse_content_length",
    "_parse_json_body",
    "_request_body_too_large_response",
    "build_agent_card",
]

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
        bg_consume_task: asyncio.Task | None = None
        try:

            async def push_notification_callback() -> None:
                await self._send_push_notification_if_needed(task_id, result_aggregator)

            (
                result,
                interrupted_or_non_blocking,
                bg_consume_task,
            ) = await result_aggregator.consume_and_break_on_interrupt(
                consumer,
                blocking=blocking,
                event_callback=push_notification_callback,
            )
            if bg_consume_task is not None:
                bg_consume_task.set_name(f"continue_consuming:{task_id}")
                self._track_background_task(bg_consume_task)
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
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelled():
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
            if params.configuration and isinstance(result, Task):
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


class KeepaliveRESTAdapter(RESTAdapter):
    def __init__(self, *args: Any, sse_ping_seconds: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sse_ping_seconds = sse_ping_seconds

    @rest_stream_error_handler
    async def _handle_streaming_request(
        self,
        method,
        request,
    ):
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as exc:
            raise ServerError(
                error=InvalidRequestError(message=f"Failed to pre-consume request body: {exc}")
            ) from exc

        call_context = self._context_builder.build(request)

        async def event_generator(
            stream: AsyncIterable[Any],
        ) -> AsyncIterator[dict[str, dict[str, Any]]]:
            async for item in stream:
                yield {"data": item}

        return EventSourceResponse(
            event_generator(method(request, call_context)),
            ping=self._sse_ping_seconds,
        )

    def routes(self) -> dict[tuple[str, str], Any]:
        routes = super().routes()

        async def message_stream(request):
            return await self._handle_streaming_request(
                self.handler.on_message_send_stream,
                request,
            )

        async def subscribe(request):
            return await self._handle_streaming_request(
                self.handler.on_resubscribe_to_task,
                request,
            )

        message_stream.__annotations__ = {"request": Request}
        subscribe.__annotations__ = {"request": Request}

        routes[("/v1/message:stream", "POST")] = message_stream
        routes[("/v1/tasks/{id}:subscribe", "GET")] = subscribe
        return routes


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
    upstream_client = OpencodeUpstreamClient(settings)
    executor = OpencodeAgentExecutor(
        upstream_client,
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
        await upstream_client.close()

    agent_card = build_agent_card(settings)
    context_builder = IdentityAwareCallContextBuilder()
    runtime_profile = build_runtime_profile(settings)
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)

    jsonrpc_methods = {
        **capability_snapshot.session_query_methods(),
        **capability_snapshot.provider_discovery_methods(),
        **capability_snapshot.interrupt_callback_methods(),
    }

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    app = OpencodeSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
        upstream_client=upstream_client,
        protocol_version=settings.a2a_protocol_version,
        supported_methods=capability_snapshot.supported_jsonrpc_methods(),
        directory_resolver=executor.resolve_directory_for_control,
        session_claim=executor.claim_session_for_control,
        session_claim_finalize=executor.finalize_session_for_control,
        session_claim_release=executor.release_session_for_control,
        methods=jsonrpc_methods,
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)
    app.state.opencode_agent_executor = executor
    _patch_jsonrpc_openapi_contract(app, settings, runtime_profile=runtime_profile)

    rest_adapter = KeepaliveRESTAdapter(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=context_builder,
        sse_ping_seconds=settings.a2a_stream_sse_ping_seconds,
    )
    for route, callback in rest_adapter.routes().items():
        app.add_api_route(route[0], callback, methods=[route[1]])

    @app.get("/health")
    async def health_check():
        return runtime_profile.health_payload(
            service="opencode-a2a-server",
            version=settings.a2a_version,
            protocol_version=settings.a2a_protocol_version,
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

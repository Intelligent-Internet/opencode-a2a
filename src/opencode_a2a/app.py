from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import jwt
import uvicorn
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from .agent import OpencodeAgentExecutor
from .config import Settings
from .jsonrpc_ext import (
    SESSION_QUERY_PAGINATION_DEFAULT_SIZE,
    SESSION_QUERY_PAGINATION_MAX_SIZE,
    OpencodeSessionQueryJSONRPCApplication,
)
from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)
ALLOWED_JWT_ALGORITHMS = {
    "RS256",
    "RS384",
    "RS512",
    "ES256",
    "ES384",
    "ES512",
}

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext

SESSION_QUERY_METHODS = {
    "list_sessions": "opencode.sessions.list",
    "get_session_messages": "opencode.sessions.messages.list",
}

SESSION_BINDING_EXTENSION_URI = "urn:opencode-a2a:opencode-session-binding/v1"


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


def build_agent_card(settings: Settings) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    base_url = public_url

    security_schemes: dict[str, SecurityScheme] = {
        "bearerAuth": SecurityScheme(
            root=HTTPAuthSecurityScheme(
                description="JWT Bearer token authentication",
                scheme="bearer",
                bearer_format="JWT",
            )
        )
    }
    security: list[dict[str, list[str]]] = [{"bearerAuth": []}]

    return AgentCard(
        name=settings.a2a_title,
        description=settings.a2a_description,
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
                        "when continuing a previous chat. "
                        "Clients should pass metadata.opencode_session_id."
                    ),
                    params={
                        "metadata_key": "opencode_session_id",
                        "behavior": "prefer_metadata_binding_else_create_session",
                        "notes": [
                            (
                                "If metadata.opencode_session_id is provided, the server will "
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
                    uri="urn:opencode-a2a:opencode-session-query/v1",
                    required=False,
                    description=(
                        "Support OpenCode session list/history queries via custom JSON-RPC methods "
                        "on the agent's A2A JSON-RPC interface."
                    ),
                    params={
                        "methods": SESSION_QUERY_METHODS,
                        "pagination": {
                            "mode": "page_size",
                            "behavior": "passthrough",
                            "params": ["page", "size"],
                            "default_size": SESSION_QUERY_PAGINATION_DEFAULT_SIZE,
                            "max_size": SESSION_QUERY_PAGINATION_MAX_SIZE,
                        },
                        "errors": {
                            "business_codes": {
                                "SESSION_NOT_FOUND": -32001,
                                "UPSTREAM_UNREACHABLE": -32002,
                                "UPSTREAM_HTTP_ERROR": -32003,
                            },
                            "error_data_fields": ["type", "session_id", "upstream_status"],
                        },
                        "result_envelope": {
                            "fields": ["items", "pagination"],
                            "items_field": "items",
                            "pagination_field": "pagination",
                        },
                    },
                ),
            ],
        ),
        skills=[
            AgentSkill(
                id="opencode.chat",
                name="OpenCode Chat",
                description="Route user messages to an OpenCode session.",
                tags=["assistant", "coding", "opencode"],
                examples=[
                    "Explain what this repository does.",
                    "Summarize the API endpoints in this project.",
                ],
            ),
            AgentSkill(
                id="opencode.sessions.query",
                name="OpenCode Sessions Query",
                description=(
                    "Query OpenCode server sessions and message histories via JSON-RPC extension "
                    "methods (see documentationUrl / extensions)."
                ),
                tags=["opencode", "sessions", "history"],
                examples=[
                    "List OpenCode sessions (method opencode.sessions.list).",
                    "List messages for a session (method opencode.sessions.messages.list).",
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


def _normalize_token_scopes(payload: dict[str, Any]) -> set[str]:
    raw_scopes = payload.get("scope")
    if raw_scopes is None:
        raw_scopes = payload.get("scp")
    if raw_scopes is None:
        return set()
    if isinstance(raw_scopes, str):
        normalized = raw_scopes.replace(",", " ")
        return {scope for scope in normalized.split() if scope}
    if isinstance(raw_scopes, list):
        token_scopes: set[str] = set()
        for scope in raw_scopes:
            normalized = str(scope).strip()
            if normalized:
                token_scopes.add(normalized)
        return token_scopes
    return set()


def add_auth_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in {
            "/.well-known/agent-card.json",
            "/.well-known/agent.json",
        }:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                {"detail": "Missing authentication credentials"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.split(" ", 1)[1].strip()

        try:
            payload = jwt.decode(
                token,
                settings.a2a_jwt_secret,
                algorithms=[settings.a2a_jwt_algorithm],
                audience=settings.a2a_jwt_audience,
                issuer=settings.a2a_jwt_issuer,
                options={"require": ["exp"]},
            )
        except jwt.PyJWTError as exc:
            logger.warning("Invalid JWT token: %s", str(exc))
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        if settings.a2a_required_scopes:
            required_scopes = set(settings.a2a_required_scopes)
            token_scopes = _normalize_token_scopes(payload)
            if settings.a2a_jwt_scope_match == "all":
                ok = required_scopes.issubset(token_scopes)
            else:
                ok = bool(required_scopes.intersection(token_scopes))
            if not ok:
                logger.warning(
                    "Token missing required scopes: %s; token_scopes=%s",
                    sorted(required_scopes),
                    sorted(token_scopes),
                )
                return JSONResponse(
                    {"detail": "Token missing required scopes"},
                    status_code=403,
                    headers={"WWW-Authenticate": "Bearer"},
                )

        identity = payload.get("sub") or payload.get("client_id") or payload.get("uid")
        if isinstance(identity, str) and identity.strip():
            request.state.user_identity = identity.strip()

        return await call_next(request)


def create_app(settings: Settings) -> FastAPI:
    if not settings.a2a_jwt_secret:
        raise RuntimeError("A2A_JWT_SECRET must be set")
    if settings.a2a_jwt_scope_match not in {"any", "all"}:
        raise RuntimeError("A2A_JWT_SCOPE_MATCH must be 'any' or 'all'")
    if settings.a2a_jwt_algorithm not in ALLOWED_JWT_ALGORITHMS:
        raise RuntimeError(f"A2A_JWT_ALGORITHM must be one of {sorted(ALLOWED_JWT_ALGORITHMS)}")
    if not settings.a2a_jwt_audience:
        raise RuntimeError("A2A_JWT_AUDIENCE must be set")
    if not settings.a2a_jwt_issuer:
        raise RuntimeError("A2A_JWT_ISSUER must be set")

    client = OpencodeClient(settings)
    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=settings.a2a_streaming,
        session_cache_ttl_seconds=settings.a2a_session_cache_ttl_seconds,
        session_cache_maxsize=settings.a2a_session_cache_maxsize,
    )
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await client.close()

    agent_card = build_agent_card(settings)

    app = OpencodeSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=IdentityAwareCallContextBuilder(),
        opencode_client=client,
        methods=SESSION_QUERY_METHODS,
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)

    rest_adapter = RESTAdapter(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=IdentityAwareCallContextBuilder(),
    )
    for route, callback in rest_adapter.routes().items():
        app.add_api_route(route[0], callback, methods=[route[1]])

    def _detect_opencode_session_query_method(body_bytes: bytes) -> str | None:
        try:
            payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        method = payload.get("method")
        if not isinstance(method, str):
            return None
        if method.startswith("opencode.sessions."):
            return method
        return None

    @app.middleware("http")
    async def log_payloads(request: Request, call_next):
        if not settings.a2a_log_payloads:
            return await call_next(request)

        body = await request.body()
        request._body = body
        path = request.url.path
        sensitive_method = _detect_opencode_session_query_method(body)

        if sensitive_method:
            logger.debug("A2A request %s %s method=%s", request.method, path, sensitive_method)
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

from __future__ import annotations

import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

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
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    SecurityScheme,
    TransportProtocol,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from .agent import OpencodeAgentExecutor
from .config import Settings
from .jsonrpc_ext import OpencodeSessionQueryJSONRPCApplication
from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext

SESSION_QUERY_METHODS = {
    "list_sessions": "opencode.sessions.list",
    "get_session_messages": "opencode.sessions.messages.list",
}


class StreamingCallContextBuilder(DefaultCallContextBuilder):
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
        return context


def build_agent_card(settings: Settings) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    base_url = public_url
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
        security_schemes = security_schemes or {}
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
        security = security or []
        security.append({"oauth2": list(settings.a2a_oauth_scopes.keys())})

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
                    uri="urn:opencode-a2a:opencode-session-query/v1",
                    required=False,
                    description=(
                        "Support OpenCode session list/history queries via custom JSON-RPC methods "
                        "on the agent's A2A JSON-RPC interface."
                    ),
                    params={
                        "methods": SESSION_QUERY_METHODS,
                        "pagination": {
                            "style": "passthrough",
                            "supported": True,
                            "params": ["page", "size", "cursor", "limit"],
                        },
                        "result_schema": None,
                    },
                )
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


def add_auth_middleware(app: FastAPI, settings: Settings) -> None:
    token = settings.a2a_bearer_token
    if not token:
        raise RuntimeError("A2A_BEARER_TOKEN must be set to start the server.")

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
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        provided = auth_header.split(" ", 1)[1].strip()
        if not secrets.compare_digest(provided, token):
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


def create_app(settings: Settings) -> FastAPI:
    client = OpencodeClient(settings)
    executor = OpencodeAgentExecutor(client, streaming_enabled=settings.a2a_streaming)
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

    # Build JSON-RPC app (POST / by default) and attach REST endpoints (HTTP+JSON) to the same app.
    app = OpencodeSessionQueryJSONRPCApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=DefaultCallContextBuilder(),
        opencode_client=client,
        methods=SESSION_QUERY_METHODS,
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)

    rest_adapter = RESTAdapter(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=StreamingCallContextBuilder(),
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
        request._body = body  # allow downstream to read again
        path = request.url.path
        sensitive_method = None
        if path == "/":
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

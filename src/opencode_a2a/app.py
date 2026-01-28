from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn
from a2a.server.apps.jsonrpc.jsonrpc_app import DefaultCallContextBuilder
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
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

from .agent import OpencodeAgentExecutor
from .config import Settings
from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext


class StreamingCallContextBuilder(DefaultCallContextBuilder):
    def build(self, request: Request) -> "ServerCallContext":
        context = super().build(request)
        if request.url.path.endswith("/v1/message:stream"):
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
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
        preferred_transport=TransportProtocol.http_json,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=settings.a2a_streaming),
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
            )
        ],
        additional_interfaces=[AgentInterface(transport=TransportProtocol.http_json, url=base_url)],
        security_schemes=security_schemes,
        security=security,
    )


def add_auth_middleware(app: FastAPI, settings: Settings) -> None:
    token = settings.a2a_bearer_token
    if not token:
        raise RuntimeError("A2A_BEARER_TOKEN must be set to start the server.")

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path == "/.well-known/agent-card.json":
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

    app = A2ARESTFastAPIApplication(
        agent_card=build_agent_card(settings),
        http_handler=handler,
        context_builder=StreamingCallContextBuilder(),
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)

    add_auth_middleware(app, settings)

    return app


settings = Settings.from_env()
app = create_app(settings)


def main() -> None:
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port)


if __name__ == "__main__":
    main()

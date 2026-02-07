from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

import jwt
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
    HTTPAuthSecurityScheme,
    SecurityScheme,
    TransportProtocol,
)
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.responses import StreamingResponse

from .agent import OpencodeAgentExecutor
from .config import Settings
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

    security_schemes: dict[str, SecurityScheme] = {}
    security: list[dict[str, list[str]]] = []

    security_schemes["bearerAuth"] = SecurityScheme(
        root=HTTPAuthSecurityScheme(
            description="JWT Bearer token authentication",
            scheme="bearer",
            bearer_format="JWT",
        )
    )
    security.append({"bearerAuth": []})

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


auth_scheme = HTTPBearer(auto_error=False)


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


async def get_auth_dependency(
    request: Request,
    auth: Annotated[HTTPAuthorizationCredentials | None, Security(auth_scheme)],
) -> Any:
    settings: Settings = request.app.state.settings
    # Public routes
    if request.url.path == "/.well-known/agent-card.json" or request.method == "OPTIONS":
        return None

    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth.credentials

    if not settings.a2a_jwt_secret:
        logger.error("A2A_JWT_SECRET is not set")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication configuration error",
        )
    try:
        decode_options: dict[str, Any] = {"require": ["exp"]}
        payload = jwt.decode(
            token,
            settings.a2a_jwt_secret,
            algorithms=[settings.a2a_jwt_algorithm],
            audience=settings.a2a_jwt_audience,
            issuer=settings.a2a_jwt_issuer,
            options=decode_options,
        )
        if settings.a2a_oauth_scopes:
            required_scopes = set(settings.a2a_oauth_scopes.keys())
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
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Token missing required scopes",
                )
        return payload
    except jwt.PyJWTError as e:
        logger.warning("Invalid JWT token: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def create_app(settings: Settings) -> FastAPI:
    # Validate auth configuration
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
    ).build(
        title=settings.a2a_title,
        version=settings.a2a_version,
        lifespan=lifespan,
        dependencies=[Depends(get_auth_dependency)],
    )
    app.state.settings = settings

    @app.middleware("http")
    async def log_payloads(request: Request, call_next):
        if not settings.a2a_log_payloads:
            return await call_next(request)

        body = await request.body()
        request._body = body  # allow downstream to read again
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

    return app


try:
    _default_settings = Settings.from_env()
    app = create_app(_default_settings)
    _default_app_error: Exception | None = None
except Exception as e:
    # Allow importing without env vars for tests, but fail fast in main().
    _default_settings = None
    _default_app_error = e
    logger.warning("Could not create default app: %s", e)
    app = None  # type: ignore[assignment]


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
    settings = _default_settings
    if settings is None:
        try:
            settings = Settings.from_env()
        except Exception as e:
            # Prefer the original import-time error if present.
            if _default_app_error is not None:
                logger.error("Failed to load settings: %s", _default_app_error)
            else:
                logger.error("Failed to load settings: %s", e)
            raise SystemExit(1) from e

    log_level = _normalize_log_level(settings.a2a_log_level)
    _configure_logging(log_level)

    runtime_app = app
    if runtime_app is None:
        try:
            runtime_app = create_app(settings)
        except Exception as e:
            logger.error("Failed to create app: %s", e)
            raise SystemExit(1) from e

    uvicorn.run(
        runtime_app,
        host=settings.a2a_host,
        port=settings.a2a_port,
        log_level=log_level.lower(),
    )


if __name__ == "__main__":
    main()

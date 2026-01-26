from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    TransportProtocol,
)
from fastapi import FastAPI

from .agent import OpencodeAgentExecutor
from .config import Settings
from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)


def build_agent_card(settings: Settings) -> AgentCard:
    public_url = settings.a2a_public_url.rstrip("/")
    base_url = public_url
    return AgentCard(
        name=settings.a2a_title,
        description=settings.a2a_description,
        url=base_url,
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
        preferred_transport=TransportProtocol.http_json,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
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
    )


def create_app(settings: Settings) -> FastAPI:
    client = OpencodeClient(settings)
    executor = OpencodeAgentExecutor(client)
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
    ).build(title=settings.a2a_title, version=settings.a2a_version, lifespan=lifespan)

    return app


settings = Settings.from_env()
app = create_app(settings)


def main() -> None:
    uvicorn.run(app, host=settings.a2a_host, port=settings.a2a_port)


if __name__ == "__main__":
    main()

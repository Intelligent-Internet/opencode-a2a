"""A2A client initialization and facade utilities for opencode-a2a consumers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast
from uuid import uuid4

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientJSONRPCError,
)
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskQueryParams,
    TaskStatusUpdateEvent,
    TextPart,
)

from .agent_card import build_resolver_http_kwargs, normalize_agent_card_endpoint
from .config import A2AClientSettings, load_settings
from .error_mapping import (
    map_agent_card_error,
    map_http_error,
    map_jsonrpc_error,
    map_operation_error,
)
from .errors import (
    A2AAgentUnavailableError,
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2AUnsupportedBindingError,
    A2AUnsupportedOperationError,
)
from .payload_text import extract_text as extract_text_from_payload
from .request_context import (
    HeaderInterceptor,
    build_call_context,
    build_client_interceptors,
    build_default_headers,
    split_request_metadata,
)
from .types import A2AClientEvent

_HeaderInterceptor = HeaderInterceptor


class A2AClient:
    """Factory-style facade for lightweight A2A client bootstrap and calls."""

    def __init__(
        self,
        agent_url: str,
        *,
        settings: A2AClientSettings | None = None,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not agent_url or not agent_url.strip():
            raise ValueError("agent_url must be non-empty")
        self.agent_url = agent_url.rstrip("/")
        self._settings = settings or load_settings({})
        self._owns_httpx_client = httpx_client is None
        self._httpx_client = httpx_client
        self._client: Client | None = None
        self._agent_card: object | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """Close cached client resources and owned HTTP transport."""
        self._client = None
        if self._httpx_client is not None and self._owns_httpx_client:
            await self._httpx_client.aclose()

    async def get_agent_card(self) -> Any:
        """Fetch and cache peer Agent Card."""
        if self._agent_card is not None:
            return self._agent_card

        await self._get_httpx_client()
        resolver = self._build_card_resolver()
        if inspect.isawaitable(resolver):
            resolver = await resolver
        try:
            card = await resolver.get_agent_card(http_kwargs=self._build_resolver_http_kwargs())
        except (
            A2AClientHTTPError,
            A2AClientJSONError,
            httpx.TimeoutException,
            httpx.TransportError,
        ) as exc:
            raise map_agent_card_error(exc) from exc
        self._agent_card = card
        return card

    async def send_message(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[A2AClientEvent]:
        """Send one user message and stream protocol events."""
        client = await self._ensure_client()
        request_metadata, extra_headers = self._split_request_metadata(metadata)
        request = self._build_user_message(
            text=text,
            context_id=context_id,
            task_id=task_id,
            message_id=message_id,
        )
        try:
            async for event in client.send_message(
                request,
                context=self._build_call_context(extra_headers),
                request_metadata=request_metadata,
                extensions=extensions,
            ):
                yield event
        except (
            A2AClientHTTPError,
            A2AClientJSONRPCError,
            httpx.TimeoutException,
            httpx.TransportError,
        ) as exc:
            raise map_operation_error("message/send", exc) from exc

    async def send(
        self,
        text: str,
        *,
        context_id: str | None = None,
        task_id: str | None = None,
        message_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> A2AClientEvent:
        """Send a message and return the terminal response/event."""
        last_event: A2AClientEvent = None
        async for event in self.send_message(
            text,
            context_id=context_id,
            task_id=task_id,
            message_id=message_id,
            metadata=metadata,
            extensions=extensions,
        ):
            last_event = event
        return last_event

    async def get_task(
        self,
        task_id: str,
        *,
        history_length: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Task:
        """Fetch one task by id."""
        client = await self._ensure_client()
        request_metadata, extra_headers = self._split_request_metadata(metadata)
        try:
            return await client.get_task(
                TaskQueryParams(
                    id=task_id,
                    history_length=history_length,
                    metadata=request_metadata or {},
                ),
                context=self._build_call_context(extra_headers),
            )
        except (
            A2AClientHTTPError,
            A2AClientJSONRPCError,
            httpx.TimeoutException,
            httpx.TransportError,
        ) as exc:
            raise map_operation_error("tasks/get", exc) from exc

    async def cancel_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Task:
        """Cancel one task by id."""
        client = await self._ensure_client()
        request_metadata, extra_headers = self._split_request_metadata(metadata)
        try:
            return await client.cancel_task(
                TaskIdParams(id=task_id, metadata=request_metadata or {}),
                context=self._build_call_context(extra_headers),
            )
        except (
            A2AClientHTTPError,
            A2AClientJSONRPCError,
            httpx.TimeoutException,
            httpx.TransportError,
        ) as exc:
            raise map_operation_error("tasks/cancel", exc) from exc

    async def resubscribe_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[tuple[Task, TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None]]:
        """Resubscribe to task updates."""
        client = await self._ensure_client()
        request_metadata, extra_headers = self._split_request_metadata(metadata)
        try:
            async for event in client.resubscribe(
                TaskIdParams(id=task_id, metadata=request_metadata or {}),
                context=self._build_call_context(extra_headers),
            ):
                yield event
        except (
            A2AClientHTTPError,
            A2AClientJSONRPCError,
            httpx.TimeoutException,
            httpx.TransportError,
        ) as exc:
            raise map_operation_error("tasks/resubscribe", exc) from exc

    async def _ensure_client(self) -> Client:
        async with self._lock:
            if self._client is not None:
                return self._client
            return await self._build_client()

    async def _build_client(self) -> Client:
        card = await self.get_agent_card()
        config = ClientConfig(
            streaming=True,
            polling=False,
            httpx_client=await self._get_httpx_client(),
            supported_transports=list(self._settings.supported_transports),
            use_client_preference=self._settings.use_client_preference,
        )
        try:
            factory = ClientFactory(config, consumers=None)
            client = factory.create(card, interceptors=self._build_interceptors())
        except ValueError as exc:
            raise A2AUnsupportedBindingError(
                f"No supported transport found for {self.agent_url}"
            ) from exc
        self._client = client
        return client

    async def _get_httpx_client(self) -> httpx.AsyncClient:
        if self._httpx_client is not None:
            return self._httpx_client
        self._httpx_client = httpx.AsyncClient(timeout=self._settings.default_timeout)
        return self._httpx_client

    def _build_card_resolver(self):
        if self._httpx_client is None:
            raise RuntimeError("HTTP client must be initialized before building card resolver")
        base_url, agent_card_path = normalize_agent_card_endpoint(self.agent_url)
        return A2ACardResolver(
            httpx_client=self._httpx_client,
            base_url=base_url,
            agent_card_path=agent_card_path,
        )

    def _build_user_message(
        self,
        *,
        text: str,
        context_id: str | None,
        task_id: str | None,
        message_id: str | None,
    ) -> Message:
        return Message(
            role=Role.user,
            message_id=message_id or str(uuid4()),
            context_id=context_id,
            task_id=task_id,
            parts=self._normalize_parts(text),
            metadata=None,
        )

    def _split_request_metadata(
        self,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        return split_request_metadata(metadata)

    def _build_call_context(
        self,
        extra_headers: Mapping[str, str] | None,
    ):
        return build_call_context(self._settings.bearer_token, extra_headers)

    def _build_default_headers(self) -> dict[str, str]:
        return build_default_headers(self._settings.bearer_token)

    def _build_interceptors(self):
        return build_client_interceptors(self._settings.bearer_token)

    def _build_resolver_http_kwargs(self) -> dict[str, Any]:
        return build_resolver_http_kwargs(
            bearer_token=self._settings.bearer_token,
            timeout=self._settings.card_fetch_timeout,
        )

    @classmethod
    def extract_text(cls, payload: Any) -> str | None:
        return extract_text_from_payload(payload)

    def _map_jsonrpc_error(
        self,
        exc: A2AClientJSONRPCError,
    ) -> A2AClientResetRequiredError | A2APeerProtocolError | A2AUnsupportedOperationError:
        mapped = map_jsonrpc_error(exc)
        if isinstance(
            mapped,
            (A2AClientResetRequiredError, A2APeerProtocolError, A2AUnsupportedOperationError),
        ):
            return mapped
        raise TypeError("Unexpected JSON-RPC error mapping result")

    def _map_http_error(
        self,
        operation: str,
        exc: A2AClientHTTPError,
    ) -> (
        A2AAgentUnavailableError
        | A2AClientResetRequiredError
        | A2APeerProtocolError
        | A2AUnsupportedOperationError
    ):
        mapped = map_http_error(operation, exc)
        if isinstance(
            mapped,
            (
                A2AAgentUnavailableError,
                A2AClientResetRequiredError,
                A2APeerProtocolError,
                A2AUnsupportedOperationError,
            ),
        ):
            return mapped
        raise TypeError("Unexpected HTTP error mapping result")

    # keep parts construction explicitly typed for mypy compatibility in older stubs
    def _normalize_parts(self, text: str) -> list[Part]:
        return [cast(Part, TextPart(text=text))]


__all__ = ["A2AClient"]

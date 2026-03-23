"""A2A client facade for opencode-a2a consumers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast
from uuid import uuid4

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
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

from .agent_card import build_agent_card_resolver, build_resolver_http_kwargs
from .config import A2AClientSettings, load_settings
from .error_mapping import (
    map_agent_card_error,
    map_operation_error,
)
from .errors import A2AUnsupportedBindingError
from .payload_text import extract_text as extract_text_from_payload
from .request_context import build_call_context, build_client_interceptors, split_request_metadata
from .types import A2AClientEvent


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
        self._request_lock = asyncio.Lock()
        self._active_requests = 0

    async def close(self) -> None:
        """Close cached client resources and owned HTTP transport."""
        self._client = None
        if self._httpx_client is not None and self._owns_httpx_client:
            await self._httpx_client.aclose()

    def is_busy(self) -> bool:
        """Report whether this facade currently has in-flight work."""
        return self._active_requests > 0

    async def get_agent_card(self) -> Any:
        """Fetch and cache peer Agent Card."""
        if self._agent_card is not None:
            return self._agent_card

        resolver = build_agent_card_resolver(
            self.agent_url,
            await self._get_httpx_client(),
        )
        try:
            card = await resolver.get_agent_card(
                http_kwargs=build_resolver_http_kwargs(
                    bearer_token=self._settings.bearer_token,
                    timeout=self._settings.card_fetch_timeout,
                )
            )
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
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers = split_request_metadata(metadata)
            request = self._build_user_message(
                text=text,
                context_id=context_id,
                task_id=task_id,
                message_id=message_id,
            )
            try:
                async for event in client.send_message(
                    request,
                    context=build_call_context(self._settings.bearer_token, extra_headers),
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
        finally:
            await self._release_operation()

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
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers = split_request_metadata(metadata)
            try:
                return await client.get_task(
                    TaskQueryParams(
                        id=task_id,
                        history_length=history_length,
                        metadata=request_metadata or {},
                    ),
                    context=build_call_context(self._settings.bearer_token, extra_headers),
                )
            except (
                A2AClientHTTPError,
                A2AClientJSONRPCError,
                httpx.TimeoutException,
                httpx.TransportError,
            ) as exc:
                raise map_operation_error("tasks/get", exc) from exc
        finally:
            await self._release_operation()

    async def cancel_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Task:
        """Cancel one task by id."""
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers = split_request_metadata(metadata)
            try:
                return await client.cancel_task(
                    TaskIdParams(id=task_id, metadata=request_metadata or {}),
                    context=build_call_context(self._settings.bearer_token, extra_headers),
                )
            except (
                A2AClientHTTPError,
                A2AClientJSONRPCError,
                httpx.TimeoutException,
                httpx.TransportError,
            ) as exc:
                raise map_operation_error("tasks/cancel", exc) from exc
        finally:
            await self._release_operation()

    async def resubscribe_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[tuple[Task, TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None]]:
        """Resubscribe to task updates."""
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers = split_request_metadata(metadata)
            try:
                async for event in client.resubscribe(
                    TaskIdParams(id=task_id, metadata=request_metadata or {}),
                    context=build_call_context(self._settings.bearer_token, extra_headers),
                ):
                    yield event
            except (
                A2AClientHTTPError,
                A2AClientJSONRPCError,
                httpx.TimeoutException,
                httpx.TransportError,
            ) as exc:
                raise map_operation_error("tasks/resubscribe", exc) from exc
        finally:
            await self._release_operation()

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
            client = factory.create(
                card,
                interceptors=build_client_interceptors(self._settings.bearer_token),
            )
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

    async def _acquire_operation(self) -> None:
        async with self._request_lock:
            self._active_requests += 1

    async def _release_operation(self) -> None:
        async with self._request_lock:
            if self._active_requests > 0:
                self._active_requests -= 1

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

    @classmethod
    def extract_text(cls, payload: Any) -> str | None:
        return extract_text_from_payload(payload)

    # keep parts construction explicitly typed for mypy compatibility in older stubs
    def _normalize_parts(self, text: str) -> list[Part]:
        return [cast(Part, TextPart(text=text))]


__all__ = ["A2AClient"]

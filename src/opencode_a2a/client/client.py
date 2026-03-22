"""A2A client initialization and facade utilities for opencode-a2a consumers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
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
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)

from .config import A2AClientSettings, load_settings
from .errors import (
    A2AAgentUnavailableError,
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2AUnsupportedBindingError,
    A2AUnsupportedOperationError,
)
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

    async def close(self) -> None:
        """Close cached client resources and owned HTTP transport."""
        self._client = None
        if self._httpx_client is not None and self._owns_httpx_client:
            await self._httpx_client.aclose()

    async def get_agent_card(self) -> Any:
        """Fetch and cache peer Agent Card."""
        if self._agent_card is not None:
            return self._agent_card

        resolver = await self._build_card_resolver()
        try:
            card = await resolver.get_agent_card(
                http_kwargs={"timeout": self._settings.card_fetch_timeout}
            )
        except A2AClientHTTPError as exc:
            raise A2AAgentUnavailableError(str(exc)) from exc
        except A2AClientJSONError as exc:
            raise A2APeerProtocolError(
                str(exc),
                error_code="invalid_agent_card",
            ) from exc
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
        request_metadata = dict(metadata) if metadata else None
        request = self._build_user_message(
            text=text,
            context_id=context_id,
            task_id=task_id,
            message_id=message_id,
        )
        try:
            async for event in client.send_message(
                request,
                request_metadata=request_metadata,
                extensions=extensions,
            ):
                yield event
        except A2AClientHTTPError as exc:
            raise self._map_http_error("message/send", exc) from exc
        except A2AClientJSONRPCError as exc:
            raise self._map_jsonrpc_error(exc) from exc

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
        try:
            return await client.get_task(
                TaskQueryParams(
                    id=task_id,
                    history_length=history_length,
                    metadata=dict(metadata or {}),
                )
            )
        except A2AClientHTTPError as exc:
            raise self._map_http_error("tasks/get", exc) from exc
        except A2AClientJSONRPCError as exc:
            raise self._map_jsonrpc_error(exc) from exc

    async def cancel_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> Task:
        """Cancel one task by id."""
        client = await self._ensure_client()
        try:
            return await client.cancel_task(TaskIdParams(id=task_id, metadata=dict(metadata or {})))
        except A2AClientHTTPError as exc:
            raise self._map_http_error("tasks/cancel", exc) from exc
        except A2AClientJSONRPCError as exc:
            raise self._map_jsonrpc_error(exc) from exc

    async def resubscribe_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[tuple[Task, TaskStatusUpdateEvent | TaskArtifactUpdateEvent | None]]:
        """Resubscribe to task updates."""
        client = await self._ensure_client()
        try:
            async for event in client.resubscribe(
                TaskIdParams(id=task_id, metadata=dict(metadata or {}))
            ):
                yield event
        except A2AClientHTTPError as exc:
            raise self._map_http_error("tasks/resubscribe", exc) from exc
        except A2AClientJSONRPCError as exc:
            raise self._map_jsonrpc_error(exc) from exc

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
            client = factory.create(card)
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

    async def _build_card_resolver(self) -> A2ACardResolver:
        parsed_url = urlsplit(self.agent_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError(f"agent_url must be absolute URL: {self.agent_url}")

        path = parsed_url.path or ""
        normalized_no_leading = path.rstrip("/").lstrip("/")
        candidate_paths = (
            AGENT_CARD_WELL_KNOWN_PATH,
            PREV_AGENT_CARD_WELL_KNOWN_PATH,
            EXTENDED_AGENT_CARD_PATH,
        )

        base_path = normalized_no_leading
        agent_card_path = AGENT_CARD_WELL_KNOWN_PATH
        for candidate_path in candidate_paths:
            card_suffix = candidate_path.lstrip("/")
            if normalized_no_leading.endswith(card_suffix):
                base_path = normalized_no_leading[: -len(card_suffix)].rstrip("/")
                agent_card_path = candidate_path
                break

        base_url = urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                f"/{base_path}" if base_path else "",
                "",
                "",
            )
        ).rstrip("/")

        return A2ACardResolver(
            httpx_client=await self._get_httpx_client(),
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

    @staticmethod
    def _extract_jsonrpc_error_payload(
        exc: A2AClientJSONRPCError,
    ) -> tuple[str, int | None, object]:
        error = getattr(exc, "error", None)
        if error is None:
            return str(exc), None, None
        return (
            str(getattr(error, "message", str(exc))),
            getattr(error, "code", None),
            getattr(error, "data", None),
        )

    def _map_jsonrpc_error(
        self,
        exc: A2AClientJSONRPCError,
    ) -> A2AUnsupportedOperationError | A2APeerProtocolError | A2AClientResetRequiredError:
        message, code, data = self._extract_jsonrpc_error_payload(exc)
        if code == -32601:
            parsed_error = A2AUnsupportedOperationError(message)
            parsed_error.error_code = "method_not_supported"
            parsed_error.code = code
            parsed_error.data = data
            return parsed_error
        if code == -32602:
            return A2APeerProtocolError(
                message,
                error_code="invalid_params",
                rpc_code=code,
                data=data,
            )
        if code == -32603:
            return A2AClientResetRequiredError(
                message,
            )
        return A2APeerProtocolError(
            message,
            error_code="peer_protocol_error",
            rpc_code=code,
            data=data,
        )

    def _map_http_error(
        self,
        operation: str,
        exc: A2AClientHTTPError,
    ) -> A2AClientResetRequiredError | A2AUnsupportedOperationError | A2AAgentUnavailableError:
        if exc.status_code in {404, 405, 409, 501}:
            parsed_error = A2AUnsupportedOperationError(f"{operation} is not supported by peer")
            parsed_error.http_status = exc.status_code
            return parsed_error
        if exc.status_code in {502, 503, 504}:
            reset_error = A2AClientResetRequiredError(
                f"{operation} failed with upstream instability"
            )
            reset_error.http_status = exc.status_code
            return reset_error
        return A2AAgentUnavailableError(str(exc))

    # keep parts construction explicitly typed for mypy compatibility in older stubs
    def _normalize_parts(self, text: str) -> list[Part]:
        return [cast(Part, TextPart(text=text))]


__all__ = ["A2AClient"]

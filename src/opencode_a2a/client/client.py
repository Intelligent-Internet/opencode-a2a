"""A2A client facade for opencode-a2a consumers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast
from uuid import uuid4

import httpx
from a2a.client import Client, ClientConfig, create_client
from a2a.client.errors import (
    A2AClientError as SDKClientError,
)
from a2a.client.errors import (
    AgentCardResolutionError,
)
from a2a.types import (
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
)
from a2a.utils.errors import A2AError

from .agent_card import build_agent_card_resolver, build_resolver_http_kwargs
from .config import A2AClientSettings, load_settings
from .error_mapping import (
    map_agent_card_error,
    map_operation_error,
)
from .errors import A2ATimeoutError, A2AUnsupportedBindingError
from .polling import PollingFallbackPolicy
from .request_context import build_call_context, split_request_metadata


def _merge_requested_extensions(
    explicit_extensions: list[str] | None,
    metadata_extensions: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    merged: list[str] = []
    for value in list(explicit_extensions or []) + list(metadata_extensions or ()):
        if isinstance(value, str):
            normalized = value.strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
    return tuple(merged) or None


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
        self._polling_fallback_policy = PollingFallbackPolicy(
            enabled=self._settings.polling_fallback_enabled,
            initial_interval_seconds=self._settings.polling_fallback_initial_interval_seconds,
            max_interval_seconds=self._settings.polling_fallback_max_interval_seconds,
            backoff_multiplier=self._settings.polling_fallback_backoff_multiplier,
            timeout_seconds=self._settings.polling_fallback_timeout_seconds,
        )

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
                    basic_auth=self._settings.basic_auth,
                )
            )
        except (
            AgentCardResolutionError,
            SDKClientError,
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
    ) -> AsyncIterator[StreamResponse]:
        """Send one user message and stream raw protocol events."""
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers, metadata_extensions = split_request_metadata(metadata)
            requested_extensions = _merge_requested_extensions(extensions, metadata_extensions)
            call_context = build_call_context(
                self._settings.bearer_token,
                extra_headers,
                requested_extensions,
                self._settings.basic_auth,
            )
            try:
                async for event in client.send_message(
                    SendMessageRequest(
                        message=Message(
                            role=Role.ROLE_USER,
                            message_id=message_id or str(uuid4()),
                            context_id=context_id,
                            task_id=task_id,
                            parts=[Part(text=text)],
                        ),
                        configuration=SendMessageConfiguration(),
                        metadata=request_metadata or {},
                    ),
                    context=call_context,
                ):
                    yield event
            except (A2AError, SDKClientError, httpx.TimeoutException, httpx.TransportError) as exc:
                raise map_operation_error("SendMessage", exc) from exc
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
    ) -> StreamResponse | None:
        """Send a message and return the latest response event.

        When polling fallback is enabled, a non-terminal task snapshot may be
        followed by bounded `GetTask` polling until a terminal task snapshot
        is observed.
        """
        last_event: StreamResponse | None = None
        async for event in self.send_message(
            text,
            context_id=context_id,
            task_id=task_id,
            message_id=message_id,
            metadata=metadata,
            extensions=extensions,
        ):
            last_event = event
        if not self._should_poll_after_send(last_event):
            return last_event
        terminal_task = await self._poll_task_until_terminal(
            cast(StreamResponse, last_event).task,
            metadata=metadata,
            extensions=extensions,
        )
        return StreamResponse(task=terminal_task)

    async def get_task(
        self,
        task_id: str,
        *,
        history_length: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Fetch one task by id."""
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers, metadata_extensions = split_request_metadata(metadata)
            requested_extensions = _merge_requested_extensions(extensions, metadata_extensions)
            call_context = build_call_context(
                self._settings.bearer_token,
                extra_headers,
                requested_extensions,
                self._settings.basic_auth,
            )
            try:
                return await client.get_task(
                    GetTaskRequest(id=task_id, history_length=history_length),
                    context=call_context,
                )
            except (A2AError, SDKClientError, httpx.TimeoutException, httpx.TransportError) as exc:
                raise map_operation_error("GetTask", exc) from exc
        finally:
            await self._release_operation()

    async def cancel_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Cancel one task by id."""
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers, metadata_extensions = split_request_metadata(metadata)
            requested_extensions = _merge_requested_extensions(extensions, metadata_extensions)
            call_context = build_call_context(
                self._settings.bearer_token,
                extra_headers,
                requested_extensions,
                self._settings.basic_auth,
            )
            try:
                return await client.cancel_task(
                    CancelTaskRequest(id=task_id, metadata=request_metadata or {}),
                    context=call_context,
                )
            except (A2AError, SDKClientError, httpx.TimeoutException, httpx.TransportError) as exc:
                raise map_operation_error("CancelTask", exc) from exc
        finally:
            await self._release_operation()

    async def subscribe_to_task(
        self,
        task_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[StreamResponse]:
        """Subscribe to task updates."""
        await self._acquire_operation()
        try:
            client = await self._ensure_client()
            request_metadata, extra_headers, metadata_extensions = split_request_metadata(metadata)
            requested_extensions = _merge_requested_extensions(extensions, metadata_extensions)
            call_context = build_call_context(
                self._settings.bearer_token,
                extra_headers,
                requested_extensions,
                self._settings.basic_auth,
            )
            try:
                async for event in client.subscribe(
                    SubscribeToTaskRequest(id=task_id),
                    context=call_context,
                ):
                    yield event
            except (A2AError, SDKClientError, httpx.TimeoutException, httpx.TransportError) as exc:
                raise map_operation_error("SubscribeToTask", exc) from exc
        finally:
            await self._release_operation()

    async def _ensure_client(self) -> Client:
        async with self._lock:
            if self._client is not None:
                return self._client
            return await self._build_client()

    async def _build_client(self) -> Client:
        config = ClientConfig(
            streaming=True,
            polling=self._polling_fallback_policy.enabled,
            httpx_client=await self._get_httpx_client(),
            supported_protocol_bindings=list(self._settings.supported_transports),
            use_client_preference=self._settings.use_client_preference,
        )
        try:
            client = await create_client(
                self.agent_url,
                client_config=config,
                resolver_http_kwargs=build_resolver_http_kwargs(
                    bearer_token=self._settings.bearer_token,
                    timeout=self._settings.card_fetch_timeout,
                    basic_auth=self._settings.basic_auth,
                ),
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

    def _should_poll_after_send(
        self,
        event: StreamResponse | None,
    ) -> bool:
        if not self._polling_fallback_policy.enabled:
            return False
        if event is None or not event.HasField("task"):
            return False
        if not event.task.HasField("status"):
            return False
        return self._polling_fallback_policy.should_poll_state(event.task.status.state)

    async def _poll_task_until_terminal(
        self,
        task: Task,
        *,
        metadata: Mapping[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        deadline = self._current_time() + self._polling_fallback_policy.timeout_seconds
        interval = self._polling_fallback_policy.initial_interval_seconds
        current_task = task

        while True:
            if self._polling_fallback_policy.is_terminal_state(current_task.status.state):
                return current_task
            if not self._polling_fallback_policy.should_poll_state(current_task.status.state):
                return current_task

            remaining = deadline - self._current_time()
            if remaining <= 0:
                raise A2ATimeoutError(
                    "Remote A2A peer did not reach a terminal task state "
                    "before polling fallback timed out"
                )

            await self._sleep(min(interval, remaining))
            current_task = await self.get_task(
                current_task.id,
                metadata=metadata,
                extensions=extensions,
            )
            interval = self._polling_fallback_policy.next_interval_seconds(interval)

    def _current_time(self) -> float:
        return asyncio.get_running_loop().time()

    async def _sleep(self, delay_seconds: float) -> None:
        await asyncio.sleep(delay_seconds)

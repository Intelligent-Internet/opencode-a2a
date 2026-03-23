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
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
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


class _HeaderInterceptor(ClientCallInterceptor):
    def __init__(self, default_headers: Mapping[str, str] | None = None) -> None:
        self._default_headers = {
            key: value for key, value in dict(default_headers or {}).items() if value is not None
        }

    async def intercept(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any],
        agent_card: object | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del method_name, agent_card
        headers = dict(http_kwargs.get("headers") or {})
        headers.update(self._default_headers)
        if context is not None:
            dynamic_headers = context.state.get("headers")
            if isinstance(dynamic_headers, Mapping):
                for key, value in dynamic_headers.items():
                    if isinstance(key, str) and value is not None:
                        headers[key] = str(value)
        if headers:
            http_kwargs["headers"] = headers
        return request_payload, http_kwargs


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
            card = await resolver.get_agent_card(http_kwargs=self._build_resolver_http_kwargs())
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
        request_metadata, extra_headers = self._split_request_metadata(metadata)
        try:
            return await client.cancel_task(
                TaskIdParams(id=task_id, metadata=request_metadata or {}),
                context=self._build_call_context(extra_headers),
            )
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
        request_metadata, extra_headers = self._split_request_metadata(metadata)
        try:
            async for event in client.resubscribe(
                TaskIdParams(id=task_id, metadata=request_metadata or {}),
                context=self._build_call_context(extra_headers),
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

    def _split_request_metadata(
        self,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        request_metadata: dict[str, Any] = {}
        extra_headers: dict[str, str] = {}
        for key, value in dict(metadata or {}).items():
            if isinstance(key, str) and key.lower() == "authorization":
                if value is not None:
                    extra_headers["Authorization"] = str(value)
                continue
            request_metadata[key] = value
        return request_metadata or None, extra_headers or None

    def _build_call_context(
        self,
        extra_headers: Mapping[str, str] | None,
    ) -> ClientCallContext | None:
        if not extra_headers:
            return None
        return ClientCallContext(state={"headers": dict(extra_headers)})

    def _build_default_headers(self) -> dict[str, str]:
        if not self._settings.bearer_token:
            return {}
        return {"Authorization": f"Bearer {self._settings.bearer_token}"}

    def _build_interceptors(self) -> list[ClientCallInterceptor] | None:
        default_headers = self._build_default_headers()
        if not default_headers:
            return None
        return [_HeaderInterceptor(default_headers)]

    def _build_resolver_http_kwargs(self) -> dict[str, Any]:
        http_kwargs: dict[str, Any] = {"timeout": self._settings.card_fetch_timeout}
        default_headers = self._build_default_headers()
        if default_headers:
            http_kwargs["headers"] = default_headers
        return http_kwargs

    @classmethod
    def extract_text(cls, payload: Any) -> str | None:
        return cls._extract_text_from_payload(payload)

    @classmethod
    def _extract_text_from_payload(cls, payload: Any) -> str | None:
        def extract_from_iterable(items: Any) -> str | None:
            if not isinstance(items, (list, tuple)):
                return None
            for item in items:
                extracted = cls._extract_text_from_payload(item)
                if extracted:
                    return extracted
            return None

        def extract_from_parts(parts: Any) -> str | None:
            if not isinstance(parts, (list, tuple)):
                return None
            collected: list[str] = []
            for part in parts:
                text_part = None
                if isinstance(part, TextPart):
                    text_part = part
                else:
                    root = getattr(part, "root", None)
                    if isinstance(root, TextPart):
                        text_part = root
                    elif isinstance(part, Mapping):
                        text_value = part.get("text")
                        if isinstance(text_value, str) and text_value.strip():
                            collected.append(text_value)
                            continue
                        mapped_root = part.get("root")
                        if isinstance(mapped_root, TextPart):
                            text_part = mapped_root
                        elif isinstance(part.get("role"), str):
                            nested = cls._extract_text_from_payload(part)
                            if nested:
                                collected.append(nested)
                                continue
                if text_part and getattr(text_part, "text", None):
                    collected.append(text_part.text)
            if collected:
                return "\n".join(collected)
            return None

        def extract_from_mapping(payload_map: Mapping[str, Any]) -> str | None:
            for key in (
                "content",
                "message",
                "messages",
                "result",
                "status",
                "text",
                "parts",
                "artifact",
                "artifacts",
                "history",
                "events",
                "root",
            ):
                if key not in payload_map:
                    continue
                value = payload_map[key]
                if value in (None, ""):
                    continue
                if key == "text" and isinstance(value, (str, int, float, bool)):
                    text_value = str(value).strip()
                    if text_value:
                        return text_value
                if key == "parts":
                    parts_text = extract_from_parts(value)
                    if parts_text:
                        return parts_text
                if key == "artifact":
                    artifact_text = cls._extract_text_from_payload(value)
                    if artifact_text:
                        return artifact_text
                if isinstance(value, (list, tuple)) and key in (
                    "messages",
                    "artifacts",
                    "history",
                    "events",
                ):
                    iterable_text = extract_from_iterable(value)
                    if iterable_text:
                        return iterable_text
                nested_text = cls._extract_text_from_payload(value)
                if nested_text:
                    return nested_text
            return None

        if isinstance(payload, (list, tuple)):
            return extract_from_iterable(payload)

        if isinstance(payload, Message):
            return extract_from_parts(payload.parts)

        if isinstance(payload, str):
            return payload.strip() or None

        status_payload = getattr(payload, "status", None)
        if status_payload is not None:
            text = cls._extract_text_from_payload(status_payload)
            if text:
                return text

        message_payload = getattr(payload, "message", None)
        if message_payload is not None:
            text = cls._extract_text_from_payload(message_payload)
            if text:
                return text

        artifact_payload = getattr(payload, "artifact", None)
        if artifact_payload is not None:
            text = cls._extract_text_from_payload(artifact_payload)
            if text:
                return text

        result_payload = getattr(payload, "result", None)
        if result_payload is not None:
            text = cls._extract_text_from_payload(result_payload)
            if text:
                return text

        history = getattr(payload, "history", None)
        if isinstance(history, (list, tuple)) and history:
            for item in reversed(history):
                text = cls._extract_text_from_payload(item)
                if text:
                    return text

        artifacts = getattr(payload, "artifacts", None)
        if isinstance(artifacts, (list, tuple)):
            for artifact in artifacts:
                artifact_parts = getattr(artifact, "parts", None)
                if isinstance(artifact_parts, (list, tuple)):
                    text = extract_from_parts(artifact_parts)
                    if text:
                        return text

        text = extract_from_parts(getattr(payload, "parts", None))
        if text:
            return text

        event_text = extract_from_iterable(getattr(payload, "events", None))
        if event_text:
            return event_text

        if isinstance(payload, Mapping):
            mapped_text = extract_from_mapping(payload)
            if mapped_text:
                return mapped_text

        mapping_payload = None
        if hasattr(payload, "model_dump") and callable(payload.model_dump):
            payload_dict = payload.model_dump()
            if isinstance(payload_dict, Mapping):
                mapping_payload = payload_dict
        elif hasattr(payload, "dict") and callable(payload.dict):
            payload_dict = payload.dict()
            if isinstance(payload_dict, Mapping):
                mapping_payload = payload_dict
        elif isinstance(getattr(payload, "__dict__", None), Mapping):
            mapping_payload = dict(payload.__dict__)

        if mapping_payload is not None:
            mapped_text = extract_from_mapping(mapping_payload)
            if mapped_text:
                return mapped_text

        return None

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

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .text_parts import extract_text_from_parts

_UNSET = object()


@dataclass(frozen=True)
class OpencodeMessage:
    text: str
    session_id: str
    message_id: str | None
    raw: dict[str, Any]


class OpencodeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.opencode_base_url.rstrip("/")
        self._directory = settings.opencode_directory
        self._provider_id = settings.opencode_provider_id
        self._model_id = settings.opencode_model_id
        self._agent = settings.opencode_agent
        self._system = settings.opencode_system
        self._variant = settings.opencode_variant
        self._stream_timeout = settings.opencode_timeout_stream
        self._log_payloads = settings.a2a_log_payloads
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.opencode_timeout,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def stream_timeout(self) -> float | None:
        return self._stream_timeout

    @property
    def directory(self) -> str | None:
        return self._directory

    @property
    def settings(self) -> Settings:
        return self._settings

    def _query_params(self, directory: str | None = None) -> dict[str, str]:
        d = directory or self._directory
        if not d:
            return {}
        return {"directory": d}

    def _merge_params(
        self, extra: dict[str, Any] | None, *, directory: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = dict(self._query_params(directory=directory))
        if not extra:
            return params
        for key, value in extra.items():
            if value is None:
                continue
            # "directory" is server-controlled. Client overrides are handled via explicit parameter.
            if key == "directory":
                continue
            # FastAPI query params are strings; keep them as-is. Coerce other primitives to str.
            params[key] = value if isinstance(value, str) else str(value)
        return params

    async def stream_events(
        self, stop_event: asyncio.Event | None = None, *, directory: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        params = self._query_params(directory=directory)
        async with self._client.stream(
            "GET",
            "/event",
            params=params,
            timeout=None,
            headers={"Accept": "text/event-stream"},
        ) as response:
            response.raise_for_status()
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if stop_event and stop_event.is_set():
                    break
                if line.startswith(":"):
                    continue
                if line == "":
                    if not data_lines:
                        continue
                    payload = "\n".join(data_lines).strip()
                    data_lines.clear()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        yield event
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue

    async def create_session(
        self, title: str | None = None, *, directory: str | None = None
    ) -> str:
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        response = await self._client.post(
            "/session", params=self._query_params(directory=directory), json=payload
        )
        response.raise_for_status()
        data = response.json()
        session_id = data.get("id")
        if not session_id:
            raise RuntimeError("OpenCode session response missing id")
        return session_id

    async def list_sessions(self, *, params: dict[str, Any] | None = None) -> Any:
        """List sessions from OpenCode."""
        # Note: directory override is not explicitly supported by list_sessions params yet.
        # If needed, we can add it later. For now we use the default.
        response = await self._client.get("/session", params=self._merge_params(params))
        response.raise_for_status()
        return response.json()

    async def list_messages(self, session_id: str, *, params: dict[str, Any] | None = None) -> Any:
        """List messages for a session from OpenCode."""
        response = await self._client.get(
            f"/session/{session_id}/message",
            params=self._merge_params(params),
        )
        response.raise_for_status()
        return response.json()

    async def send_message(
        self,
        session_id: str,
        text: str,
        *,
        directory: str | None = None,
        timeout_override: float | None | object = _UNSET,
    ) -> OpencodeMessage:
        payload: dict[str, Any] = {
            "parts": [
                {
                    "type": "text",
                    "text": text,
                }
            ]
        }
        if self._provider_id and self._model_id:
            payload["model"] = {
                "providerID": self._provider_id,
                "modelID": self._model_id,
            }
        if self._agent:
            payload["agent"] = self._agent
        if self._system:
            payload["system"] = self._system
        if self._variant:
            payload["variant"] = self._variant

        if self._log_payloads:
            logger = logging.getLogger(__name__)
            logger.debug("OpenCode request payload=%s", payload)

        request_kwargs: dict[str, Any] = {}
        if timeout_override is not _UNSET:
            request_kwargs["timeout"] = timeout_override

        response = await self._client.post(
            f"/session/{session_id}/message",
            params=self._query_params(directory=directory),
            json=payload,
            **request_kwargs,
        )
        response.raise_for_status()
        data = response.json()
        if self._log_payloads:
            logger = logging.getLogger(__name__)
            logger.debug("OpenCode response payload=%s", data)
        text_content = extract_text_from_parts(data.get("parts", []))
        message_id = None
        info = data.get("info")
        if isinstance(info, dict):
            message_id = info.get("id")
        return OpencodeMessage(
            text=text_content,
            session_id=session_id,
            message_id=message_id,
            raw=data,
        )

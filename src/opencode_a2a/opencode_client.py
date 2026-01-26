from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


@dataclass(frozen=True)
class OpencodeMessage:
    text: str
    session_id: str
    message_id: str | None
    raw: dict[str, Any]


class OpencodeClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.opencode_base_url.rstrip("/")
        self._directory = settings.opencode_directory
        self._provider_id = settings.opencode_provider_id
        self._model_id = settings.opencode_model_id
        self._agent = settings.opencode_agent
        self._system = settings.opencode_system
        self._variant = settings.opencode_variant
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=settings.opencode_timeout,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _query_params(self) -> dict[str, str]:
        if not self._directory:
            return {}
        return {"directory": self._directory}

    async def create_session(self, title: str | None = None) -> str:
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        response = await self._client.post("/session", params=self._query_params(), json=payload)
        response.raise_for_status()
        data = response.json()
        session_id = data.get("id")
        if not session_id:
            raise RuntimeError("OpenCode session response missing id")
        return session_id

    async def send_message(self, session_id: str, text: str) -> OpencodeMessage:
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

        response = await self._client.post(
            f"/session/{session_id}/message",
            params=self._query_params(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("parts", [])
        text_content = _extract_text(parts)
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


def _extract_text(parts: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for part in parts:
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "".join(texts).strip()

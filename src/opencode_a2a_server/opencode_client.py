from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .parts.text import extract_text_from_parts

_UNSET = object()
logger = logging.getLogger(__name__)


class UpstreamContractError(RuntimeError):
    """Raised when upstream returns a shape/status that violates documented contract."""


@dataclass(frozen=True)
class OpencodeMessage:
    text: str
    session_id: str
    message_id: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class InterruptRequestBinding:
    request_id: str
    session_id: str
    interrupt_type: str
    identity: str | None
    task_id: str | None
    context_id: str | None
    expires_at: float


class OpencodeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.opencode_base_url.rstrip("/")
        self._directory = settings.opencode_workspace_root
        self._agent = settings.opencode_agent
        self._system = settings.opencode_system
        self._variant = settings.opencode_variant
        self._stream_timeout = settings.opencode_timeout_stream
        self._log_payloads = settings.a2a_log_payloads
        self._interrupt_request_ttl_seconds = 600.0
        self._interrupt_request_clock = time.monotonic
        self._interrupt_requests: dict[str, InterruptRequestBinding] = {}
        self._client = self._build_http_client(self._base_url)

    def _build_http_client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=self._settings.opencode_timeout,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _response_body_preview(response: httpx.Response, *, limit: int = 200) -> str:
        body = response.text.strip()
        if not body:
            return "<empty>"
        compact = " ".join(body.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 3]}..."

    def _decode_json_response(self, response: httpx.Response, *, endpoint: str) -> Any:
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            normalized_content_type = content_type or "unknown"
            body_preview = self._response_body_preview(response)
            raise UpstreamContractError(
                f"OpenCode {endpoint} returned non-JSON response "
                f"(status={response.status_code}, content-type={normalized_content_type}, "
                f"body={body_preview})"
            ) from exc

    @staticmethod
    def _require_boolean_response(*, endpoint: str, payload: Any) -> bool:
        if isinstance(payload, bool):
            return payload
        raise RuntimeError(
            f"OpenCode {endpoint} response must be boolean; got {type(payload).__name__}"
        )

    async def _get_json(
        self,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return self._decode_json_response(response, endpoint=endpoint)

    async def _post_json(
        self,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = _UNSET,
        timeout: float | None | object = _UNSET,
    ) -> Any:
        request_kwargs: dict[str, Any] = {}
        if json_body is not _UNSET:
            request_kwargs["json"] = json_body
        if timeout is not _UNSET:
            request_kwargs["timeout"] = timeout
        response = await self._client.post(
            path,
            params=params,
            **request_kwargs,
        )
        response.raise_for_status()
        return self._decode_json_response(response, endpoint=endpoint)

    async def _post_boolean(
        self,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = _UNSET,
        timeout: float | None | object = _UNSET,
    ) -> bool:
        data = await self._post_json(
            path,
            endpoint=endpoint,
            params=params,
            json_body=json_body,
            timeout=timeout,
        )
        return self._require_boolean_response(endpoint=endpoint, payload=data)

    def _prune_interrupt_requests(self, *, now: float) -> None:
        expired = [
            request_id
            for request_id, binding in self._interrupt_requests.items()
            if binding.expires_at <= now
        ]
        for request_id in expired:
            self._interrupt_requests.pop(request_id, None)

    def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        request = request_id.strip()
        session = session_id.strip()
        kind = interrupt_type.strip()
        if not request or not session or kind not in {"permission", "question"}:
            return
        now = self._interrupt_request_clock()
        self._prune_interrupt_requests(now=now)
        ttl = self._interrupt_request_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = now + max(0.0, float(ttl))
        self._interrupt_requests[request] = InterruptRequestBinding(
            request_id=request,
            session_id=session,
            interrupt_type=kind,
            identity=identity.strip() if isinstance(identity, str) and identity.strip() else None,
            task_id=task_id.strip() if isinstance(task_id, str) and task_id.strip() else None,
            context_id=(
                context_id.strip() if isinstance(context_id, str) and context_id.strip() else None
            ),
            expires_at=expires_at,
        )

    def resolve_interrupt_request(
        self,
        request_id: str,
    ) -> tuple[str, InterruptRequestBinding | None]:
        request = request_id.strip()
        if not request:
            return "missing", None
        now = self._interrupt_request_clock()
        binding = self._interrupt_requests.get(request)
        if binding is None:
            return "missing", None
        if binding.expires_at <= now:
            self._interrupt_requests.pop(request, None)
            self._prune_interrupt_requests(now=now)
            return "expired", None
        self._prune_interrupt_requests(now=now)
        return "active", binding

    def resolve_interrupt_session(self, request_id: str) -> str | None:
        status, binding = self.resolve_interrupt_request(request_id)
        if status != "active" or binding is None:
            return None
        return binding.session_id

    def discard_interrupt_request(self, request_id: str) -> None:
        request = request_id.strip()
        if not request:
            return
        self._interrupt_requests.pop(request, None)

    @property
    def stream_timeout(self) -> float | None:
        return self._stream_timeout

    @property
    def directory(self) -> str | None:
        return self._directory

    @property
    def settings(self) -> Settings:
        return self._settings

    @staticmethod
    def _normalize_model_ref(value: Mapping[str, Any] | None) -> dict[str, str] | None:
        if value is None:
            return None
        provider = value.get("providerID")
        model = value.get("modelID")
        if not isinstance(provider, str) or not isinstance(model, str):
            return None
        provider_id = provider.strip()
        model_id = model.strip()
        if not provider_id or not model_id:
            return None
        return {
            "providerID": provider_id,
            "modelID": model_id,
        }

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
        data = await self._post_json(
            "/session",
            endpoint="/session",
            params=self._query_params(directory=directory),
            json_body=payload,
        )
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("OpenCode session response missing id")
        return session_id

    async def abort_session(self, session_id: str, *, directory: str | None = None) -> bool:
        return await self._post_boolean(
            f"/session/{session_id}/abort",
            endpoint="/session/{sessionID}/abort",
            params=self._query_params(directory=directory),
        )

    async def list_sessions(self, *, params: dict[str, Any] | None = None) -> Any:
        """List sessions from OpenCode."""
        # Note: directory override is not explicitly supported by list_sessions params yet.
        # If needed, we can add it later. For now we use the default.
        return await self._get_json(
            "/session",
            endpoint="/session",
            params=self._merge_params(params),
        )

    async def list_messages(self, session_id: str, *, params: dict[str, Any] | None = None) -> Any:
        """List messages for a session from OpenCode."""
        return await self._get_json(
            f"/session/{session_id}/message",
            endpoint="/session/{sessionID}/message",
            params=self._merge_params(params),
        )

    async def session_prompt_async(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> None:
        response = await self._client.post(
            f"/session/{session_id}/prompt_async",
            params=self._query_params(directory=directory),
            json=request,
        )
        response.raise_for_status()
        if response.status_code != 204:
            raise UpstreamContractError(
                "OpenCode /session/{sessionID}/prompt_async must return 204; "
                f"got {response.status_code}"
            )

    async def session_command(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> Any:
        return await self._post_json(
            f"/session/{session_id}/command",
            endpoint="/session/{sessionID}/command",
            params=self._query_params(directory=directory),
            json_body=request,
        )

    async def session_shell(
        self,
        session_id: str,
        request: dict[str, Any],
        *,
        directory: str | None = None,
    ) -> Any:
        return await self._post_json(
            f"/session/{session_id}/shell",
            endpoint="/session/{sessionID}/shell",
            params=self._query_params(directory=directory),
            json_body=request,
        )

    async def list_provider_catalog(self, *, directory: str | None = None) -> Any:
        return await self._get_json(
            "/provider",
            endpoint="/provider",
            params=self._query_params(directory=directory),
        )

    async def send_message(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: Sequence[Mapping[str, Any]] | None = None,
        directory: str | None = None,
        model_override: Mapping[str, Any] | None = None,
        timeout_override: float | None | object = _UNSET,
    ) -> OpencodeMessage:
        payload_parts: list[dict[str, Any]]
        if parts is not None:
            payload_parts = [dict(part) for part in parts]
        elif isinstance(text, str):
            payload_parts = [
                {
                    "type": "text",
                    "text": text,
                }
            ]
        else:
            raise ValueError("send_message requires either text or parts")

        if not payload_parts:
            raise ValueError("send_message parts must not be empty")

        payload: dict[str, Any] = {"parts": payload_parts}
        if self._agent:
            payload["agent"] = self._agent
        if self._system:
            payload["system"] = self._system
        if self._variant:
            payload["variant"] = self._variant
        normalized_model = self._normalize_model_ref(model_override)
        if normalized_model is not None:
            payload["model"] = normalized_model

        if self._log_payloads:
            logger.debug("OpenCode request payload=%s", payload)

        data = await self._post_json(
            f"/session/{session_id}/message",
            endpoint="/session/{sessionID}/message",
            params=self._query_params(directory=directory),
            json_body=payload,
            timeout=timeout_override,
        )
        if self._log_payloads:
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

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
    ) -> bool:
        payload: dict[str, Any] = {"reply": reply}
        if message:
            payload["message"] = message
        return await self._post_boolean(
            f"/permission/{request_id}/reply",
            endpoint="/permission/{requestID}/reply",
            params=self._query_params(directory=directory),
            json_body=payload,
        )

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
    ) -> bool:
        return await self._post_boolean(
            f"/question/{request_id}/reply",
            endpoint="/question/{requestID}/reply",
            params=self._query_params(directory=directory),
            json_body={"answers": answers},
        )

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
    ) -> bool:
        return await self._post_boolean(
            f"/question/{request_id}/reject",
            endpoint="/question/{requestID}/reject",
            params=self._query_params(directory=directory),
        )

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import quote

import httpx

from ..config import Settings
from ..opencode_upstream_client import (
    OpencodeMessage,
    OpencodeUpstreamClient,
    UpstreamContractError,
)
from ..sandbox_policy import SandboxPolicy
from ..server.state_store import InterruptRequestRepository
from .contracts import PromptSubmission


class UnsupportedUpstreamOperationError(RuntimeError):
    """Raised when the selected upstream protocol has no implemented operation."""


class V2UpstreamAdapter(OpencodeUpstreamClient):
    """Explicit V2 session transport using the shared V1 HTTP facade infrastructure."""

    prompt_execution_supported = False

    def __init__(
        self,
        settings: Settings,
        *,
        interrupt_request_repository: InterruptRequestRepository | None = None,
    ) -> None:
        super().__init__(settings, interrupt_request_repository=interrupt_request_repository)
        self._v2_server_info: Mapping[str, Any] | None = None
        self._v2_server_info_lock = asyncio.Lock()

    def _resolve_location(self, directory: str | None, workspace_id: str | None) -> str:
        if workspace_id is not None and workspace_id.strip():
            raise UnsupportedUpstreamOperationError(
                "OpenCode V2 session transport does not support V1 workspace IDs."
            )
        return (
            SandboxPolicy.from_settings(
                self.settings,
                workspace_root=self.directory,
            ).resolve_directory(directory, default_directory=self.directory)
            or ""
        )

    @staticmethod
    def _unwrap_data(payload: Any, *, endpoint: str) -> Any:
        if not isinstance(payload, Mapping) or "data" not in payload:
            raise UpstreamContractError(
                f"OpenCode V2 {endpoint} response must contain a data envelope"
            )
        return payload["data"]

    async def _get_v2_json(
        self,
        path: str,
        *,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        if endpoint != "/api/info":
            await self.get_server_info()
        try:
            return await self._get_json(path, endpoint=endpoint, params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise UpstreamContractError(
                    f"OpenCode V2 {endpoint} returned HTTP 404; verify the upstream is an "
                    "OpenCode V2 server and the requested location/session exists"
                ) from exc
            raise

    async def _post_v2_json(
        self,
        path: str,
        *,
        endpoint: str,
        json_body: Any,
    ) -> Any:
        await self.get_server_info()
        try:
            return await self._post_json(
                path,
                endpoint=endpoint,
                json_body=json_body,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise UpstreamContractError(
                    f"OpenCode V2 {endpoint} returned HTTP 404; verify the upstream is an "
                    "OpenCode V2 server and the requested location/session exists"
                ) from exc
            raise

    async def get_server_info(self) -> Mapping[str, Any]:
        async with self._v2_server_info_lock:
            if self._v2_server_info is not None:
                return self._v2_server_info
            payload = await self._get_v2_json("/api/info", endpoint="/api/info")
            version = payload.get("version") if isinstance(payload, Mapping) else None
            if not isinstance(version, str):
                raise UpstreamContractError(
                    "OpenCode V2 /api/info response must include a string version"
                )
            if not version.startswith("2."):
                raise UpstreamContractError(
                    f"OpenCode V2 was selected but /api/info reports version {version!r}"
                )
            server_info = cast(Mapping[str, Any], payload)
            self._v2_server_info = server_info
            return server_info

    async def get_location(self, *, directory: str | None = None) -> Mapping[str, Any]:
        resolved_directory = self._resolve_location(directory, None)
        payload = await self._get_v2_json(
            "/api/location",
            endpoint="/api/location",
            params={"location[directory]": resolved_directory},
        )
        if not isinstance(payload, Mapping):
            raise UpstreamContractError("OpenCode V2 /api/location response must be an object")
        return payload

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> str:
        resolved_directory = self._resolve_location(directory, workspace_id)
        payload: dict[str, Any] = {"location": {"directory": resolved_directory}}
        if title:
            payload["title"] = title
        response = await self._post_v2_json(
            "/api/session",
            endpoint="/api/session",
            json_body=payload,
        )
        data = self._unwrap_data(response, endpoint="/api/session")
        session_id = data.get("id") if isinstance(data, Mapping) else None
        if not isinstance(session_id, str) or not session_id.strip():
            raise UpstreamContractError("OpenCode V2 /api/session data is missing session id")
        return session_id

    async def get_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> Any:
        normalized_id = session_id.strip()
        if not normalized_id:
            raise ValueError("session_id must not be empty")
        resolved_directory = self._resolve_location(directory, workspace_id)
        path = f"/api/session/{quote(normalized_id, safe='')}"
        response = await self._get_v2_json(
            path,
            endpoint="/api/session/{sessionID}",
            params={"location[directory]": resolved_directory},
        )
        return self._unwrap_data(response, endpoint="/api/session/{sessionID}")

    async def list_sessions(
        self,
        *,
        params: dict[str, Any] | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> Any:
        resolved_directory = self._resolve_location(directory, workspace_id)
        query: dict[str, Any] = {"location[directory]": resolved_directory}
        if params:
            if any(
                key in {"directory", "workspace", "location"} or key.startswith("location[")
                for key in params
            ):
                raise ValueError("V2 session list location is selected by the location parameter")
            query.update(params)
        response = await self._get_v2_json(
            "/api/session",
            endpoint="/api/session",
            params=query,
        )
        data = self._unwrap_data(response, endpoint="/api/session")
        if not isinstance(data, list):
            raise UpstreamContractError("OpenCode V2 /api/session data must be an array")
        return {"data": data, **{key: value for key, value in response.items() if key != "data"}}

    async def send_message(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: Sequence[Mapping[str, Any]] | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
        model_override: Mapping[str, Any] | None = None,
        timeout_override: float | None | object = ...,
    ) -> OpencodeMessage:
        del session_id, text, parts, directory, workspace_id, model_override, timeout_override
        raise UnsupportedUpstreamOperationError(
            "OpenCode V2 prompt execution is not implemented; use OPENCODE_API_VERSION=v1 "
            "or wait for the V2 execution support release."
        )

    async def submit_prompt(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: Sequence[Mapping[str, Any]] | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
        model_override: Mapping[str, Any] | None = None,
        timeout_override: float | None | object = ...,
    ) -> PromptSubmission:
        del session_id, text, parts, directory, workspace_id, model_override, timeout_override
        raise UnsupportedUpstreamOperationError(
            "OpenCode V2 prompt execution is not implemented; use OPENCODE_API_VERSION=v1 "
            "or wait for the V2 execution support release."
        )

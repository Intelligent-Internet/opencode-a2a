from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
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
from .contracts import ExecutionUpdate, PromptSubmission
from .v2_events import V2EventHub, V2ExecutionUncertainError
from .v2_execution import V2ExecutionEngine
from .v2_interventions import V2Interventions
from .v2_profile import (
    exclusive_permissions,
    validate_exclusive_configuration,
    validate_exclusive_settings,
)


class UnsupportedUpstreamOperationError(RuntimeError):
    """Raised when the selected upstream protocol has no implemented operation."""


class V2UpstreamAdapter(OpencodeUpstreamClient):
    """V2 session transport with an explicitly selected private execution profile."""

    prompt_execution_supported = False
    prompt_execution_unsupported_reason = (
        "OpenCode V2 prompt execution is disabled: a caller-supplied input ID identifies its "
        "inbox/user message, but the upstream contract does not prove a per-input terminal "
        "outcome after event loss or support task-scoped active cancellation. "
        "No prompt was submitted."
    )

    def __init__(
        self,
        settings: Settings,
        *,
        interrupt_request_repository: InterruptRequestRepository | None = None,
    ) -> None:
        super().__init__(settings, interrupt_request_repository=interrupt_request_repository)
        self._v2_server_info: Mapping[str, Any] | None = None
        self._v2_server_info_lock = asyncio.Lock()
        self.prompt_execution_supported = settings.opencode_v2_execution == "exclusive"
        self.supports_streaming_submission = self.prompt_execution_supported
        if self.prompt_execution_supported:
            validate_exclusive_settings(settings)
        self._v2_hub = V2EventHub(self)
        self._v2_execution = V2ExecutionEngine(
            self, self._v2_hub, timeout=settings.opencode_timeout_stream or 900
        )
        self._v2_interventions = V2Interventions(
            self, validate_lease=self._v2_execution.validate_intervention
        )
        self._v2_creation_lock = asyncio.Lock()
        self._v2_invocation_tasks: dict[str, asyncio.Task[object]] = {}

    async def close(self) -> None:
        await self._v2_hub.close()
        await super().close()

    async def _verify_exclusive_profile(self, directory: str) -> None:
        info = await self.get_server_info()
        if info.get("version") != "2.0.28":
            raise UpstreamContractError("V2 exclusive execution is pinned to OpenCode 2.0.28")
        query = {"location[directory]": directory}
        entries = await self._get_v2_json("/api/config", endpoint="/api/config", params=query)
        plugins = await self._get_v2_json("/api/plugin", endpoint="/api/plugin", params=query)
        validate_exclusive_configuration(entries, plugins)

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
        if self.prompt_execution_supported:
            await self._verify_exclusive_profile(resolved_directory)
            payload["permissions"] = exclusive_permissions()
        if title:
            payload["title"] = title
        async with self._v2_creation_lock:
            if self.prompt_execution_supported:
                self._v2_execution.ensure_capacity()
            response = await self._post_v2_json(
                "/api/session",
                endpoint="/api/session",
                json_body=payload,
            )
            data = self._unwrap_data(response, endpoint="/api/session")
            session_id = data.get("id") if isinstance(data, Mapping) else None
            if not isinstance(session_id, str) or not session_id.strip():
                raise UpstreamContractError("OpenCode V2 /api/session data is missing session id")
            if self.prompt_execution_supported:
                self._v2_execution.register_created(session_id, resolved_directory)
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
        raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)

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
        stream_updates_requested: bool = False,
    ) -> PromptSubmission:
        if not self.prompt_execution_supported:
            raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)
        if session_id not in self._v2_execution.sessions:
            raise UnsupportedUpstreamOperationError(
                "V2 execution requires a gateway-created session"
            )
        if model_override or (parts and any(part.get("type") != "text" for part in parts)):
            raise UnsupportedUpstreamOperationError(
                "V2 exclusive execution supports text only, without request-scoped model overrides"
            )
        if not isinstance(text, str) or not text.strip():
            raise ValueError("V2 exclusive execution requires nonempty text")
        if parts and any(not isinstance(part.get("text"), str) for part in parts):
            raise ValueError("V2 text parts require string text")
        resolved = self._resolve_location(directory, workspace_id)
        await self._verify_exclusive_profile(resolved)
        session = await self.get_session(session_id, directory=resolved)
        if (
            not isinstance(session, Mapping)
            or session.get("permissions") != exclusive_permissions()
        ):
            raise UpstreamContractError("V2 session permissions do not match the exclusive profile")
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("V2 execution requires an owned asyncio task")
        self._v2_invocation_tasks[session_id] = current
        try:
            return await self._v2_execution.submit(
                session_id, text, emit_updates=stream_updates_requested
            )
        finally:
            if self._v2_invocation_tasks.get(session_id) is current:
                self._v2_invocation_tasks.pop(session_id, None)

    async def stream_updates(
        self,
        stop_event: asyncio.Event | None = None,
        *,
        session_id: str | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> AsyncIterator[ExecutionUpdate]:
        from ..execution.stream_events import normalize_v1_event

        self._resolve_location(directory, workspace_id)
        if not self.prompt_execution_supported:
            raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)
        if session_id is None:
            raise ValueError("V2 updates require a gateway-owned session")
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            event = await self._v2_execution.next_update(session_id)
            if isinstance(event, ExecutionUpdate):
                yield event
                continue
            try:
                canonical = self._v2_interventions.normalize_event(event, session_id=session_id)
                if canonical is not None and canonical.get("type") in {
                    "permission.asked",
                    "question.asked",
                }:
                    tool = canonical["properties"].get("tool")
                    message_id = tool.get("messageID") if isinstance(tool, Mapping) else None
                    if not isinstance(message_id, str):
                        raise ValueError("V2 intervention requires an observed assistant tool")
                    self._v2_execution.validate_intervention(
                        session_id, assistant_message_id=message_id
                    )
            except (ValueError, V2ExecutionUncertainError) as exc:
                self._v2_execution.reject_intervention(session_id)
                raise UpstreamContractError(str(exc)) from exc
            update = normalize_v1_event(canonical or event, log_limit=0)
            if (
                canonical is not None
                and canonical.get("type") == "question.asked"
                and update is not None
                and update.intervention is not None
            ):
                update = replace(
                    update,
                    intervention=replace(
                        update.intervention,
                        details={"questions": canonical["properties"]["questions"]},
                    ),
                )
            if update is not None:
                yield update

    async def abort_session(
        self,
        session_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        self._resolve_location(directory, workspace_id)
        if not self.prompt_execution_supported:
            raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)
        result = await self._v2_execution.abort(session_id)
        return result.outcome is not None and result.outcome.state.value == "cancelled"

    async def cancel_invocation(
        self, session_id: str, execution_task: asyncio.Task[object], *, directory: str | None
    ) -> PromptSubmission:
        self._resolve_location(directory, None)
        if self._v2_invocation_tasks.get(session_id) is not execution_task:
            raise RuntimeError("Cancellation no longer owns the active invocation")
        identity = self._v2_execution.invocation_id(session_id)
        return await self._v2_execution.abort(session_id, expected_input_id=identity)

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        self._resolve_location(directory, workspace_id)
        if not self.prompt_execution_supported:
            raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)
        await self._v2_interventions.permission_reply(request_id, reply=reply, message=message)
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: Sequence[Sequence[str]],
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        self._resolve_location(directory, workspace_id)
        if not self.prompt_execution_supported:
            raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)
        await self._v2_interventions.question_reply(
            request_id, answers=[list(answer) for answer in answers]
        )
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        self._resolve_location(directory, workspace_id)
        if not self.prompt_execution_supported:
            raise UnsupportedUpstreamOperationError(self.prompt_execution_unsupported_reason)
        await self._v2_interventions.question_reject(request_id)
        return True

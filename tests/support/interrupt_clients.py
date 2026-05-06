from __future__ import annotations

from typing import Any


class InterruptRequestClientMixin:
    _interrupt_requests: dict[str, dict[str, str | None]]
    _interrupt_request_details: dict[str, dict[str, Any] | None]

    async def remember_interrupt_request(
        self,
        *,
        request_id: str,
        session_id: str,
        interrupt_type: str,
        identity: str | None = None,
        credential_id: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        details: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        del ttl_seconds
        self._interrupt_requests[request_id] = {
            "session_id": session_id,
            "interrupt_type": interrupt_type,
            "identity": identity,
            "credential_id": credential_id,
            "task_id": task_id,
            "context_id": context_id,
        }
        self._interrupt_request_details[request_id] = (
            dict(details) if isinstance(details, dict) else None
        )

    async def resolve_interrupt_request(self, request_id: str):
        payload = self._interrupt_requests.get(request_id)
        if payload is None:
            return "missing", None
        details = self._interrupt_request_details.get(request_id)

        class _Binding:
            def __init__(self, data: dict[str, str | None]) -> None:
                self.request_id = request_id
                self.session_id = data.get("session_id")
                self.interrupt_type = data.get("interrupt_type")
                self.identity = data.get("identity")
                self.credential_id = data.get("credential_id")
                self.task_id = data.get("task_id")
                self.context_id = data.get("context_id")
                self.details = details

        return "active", _Binding(payload)

    async def resolve_interrupt_session(self, request_id: str) -> str | None:
        payload = self._interrupt_requests.get(request_id)
        if payload is None:
            return None
        return payload.get("session_id")

    async def discard_interrupt_request(self, request_id: str) -> None:
        self._interrupt_requests.pop(request_id, None)
        self._interrupt_request_details.pop(request_id, None)

    async def list_interrupt_requests(
        self,
        *,
        identity: str,
        interrupt_type: str | None = None,
    ):
        class _Binding:
            def __init__(
                self,
                *,
                request_id: str,
                data: dict[str, str | None],
                details: dict[str, Any] | None,
            ) -> None:
                self.request_id = request_id
                self.session_id = data.get("session_id")
                self.interrupt_type = data.get("interrupt_type")
                self.identity = data.get("identity")
                self.credential_id = data.get("credential_id")
                self.task_id = data.get("task_id")
                self.context_id = data.get("context_id")
                self.details = details
                self.expires_at = 0.0

        items = []
        for request_id, payload in self._interrupt_requests.items():
            if payload.get("identity") != identity:
                continue
            if interrupt_type is not None and payload.get("interrupt_type") != interrupt_type:
                continue
            items.append(
                _Binding(
                    request_id=request_id,
                    data=payload,
                    details=self._interrupt_request_details.get(request_id),
                )
            )
        return items

    async def list_permission_requests(self, *, identity: str):
        return await self.list_interrupt_requests(identity=identity, interrupt_type="permission")

    async def list_question_requests(self, *, identity: str):
        return await self.list_interrupt_requests(identity=identity, interrupt_type="question")

    async def permission_reply(
        self,
        request_id: str,
        *,
        reply: str,
        message: str | None = None,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        del request_id, reply, message, directory, workspace_id
        return True

    async def question_reply(
        self,
        request_id: str,
        *,
        answers: list[list[str]],
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        del request_id, answers, directory, workspace_id
        return True

    async def question_reject(
        self,
        request_id: str,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        del request_id, directory, workspace_id
        return True

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from ..interrupt_request_tracker import BoundInterruptRequestTracker
from ..opencode_upstream_client import UpstreamContractError

if TYPE_CHECKING:
    from .v2 import V2UpstreamAdapter


class V2Interventions:
    def __init__(
        self, client: V2UpstreamAdapter, *, validate_lease: Callable[[str], object] | None = None
    ) -> None:
        self._client = client
        self._tracker = BoundInterruptRequestTracker(client)
        self._pending: dict[tuple[str, str], tuple[str, list[dict[str, object]]]] = {}
        self._settled: set[tuple[str, str]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._validate_lease = validate_lease

    @staticmethod
    def _fields(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list) or not value:
            raise ValueError("Question form fields must be a nonempty array")
        fields: list[dict[str, object]] = []
        allowed = {"key", "type", "title", "description", "required", "options", "custom"}
        for index, field in enumerate(value):
            if not isinstance(field, dict) or set(field) - allowed:
                raise ValueError("Unsupported question form field constraints")
            if field.get("key") != f"q{index}" or field.get("type") not in {
                "string",
                "multiselect",
            }:
                raise ValueError("Unsupported question form field type or key")
            if any(key in field and type(field[key]) is not bool for key in ("custom", "required")):
                raise ValueError("Question field flags must be boolean")
            if any(
                key in field and not isinstance(field[key], str) for key in ("title", "description")
            ):
                raise ValueError("Question field labels must be strings")
            options = field.get("options", [])
            if (
                not isinstance(options, list)
                or any(
                    not isinstance(option, dict)
                    or not isinstance(option.get("value"), str)
                    or not isinstance(option.get("label"), str)
                    or ("description" in option and not isinstance(option["description"], str))
                    for option in options
                )
                or (field["type"] == "multiselect" and "options" not in field)
            ):
                raise ValueError("Question field options must be string options")
            fields.append(deepcopy(field))
        return fields

    def normalize_event(self, event: Mapping[str, object], *, session_id: str) -> dict | None:
        kind, props = event.get("type"), event.get("properties")
        if not isinstance(props, Mapping):
            return None
        form = props.get("form") if kind == "form.created" else props
        if not isinstance(form, Mapping) or form.get("sessionID") != session_id:
            return None
        request = form.get("requestID") if kind == "permission.replied" else form.get("id")
        if not isinstance(request, str) or not request:
            return None
        key = (session_id, request)
        if kind in {"form.replied", "form.cancelled", "permission.replied"}:
            pending = self._pending.pop(key, None)
            self._settled.discard(key)
            self._locks.pop(request, None)
            if pending is None:
                return None
            resolved = "question.rejected" if kind == "form.cancelled" else f"{pending[0]}.replied"
            return {"type": resolved, "properties": {**props, "requestID": request}}
        if kind == "permission.asked":
            if len(self._pending) >= 1024:
                raise ValueError("V2 pending intervention capacity exceeded")
            resources = form.get("resources")
            if not isinstance(form.get("action"), str) or not isinstance(resources, list):
                raise ValueError("Invalid permission request")
            if any(not isinstance(resource, str) for resource in resources):
                raise ValueError("Permission resources must be strings")
            details = {**form, "permission": form["action"], "patterns": form["resources"]}
            source = form.get("source")
            if isinstance(source, Mapping) and source.get("type") == "tool":
                details["tool"] = {"messageID": source.get("messageID"), "callID": source.get("id")}
            self._pending[key] = ("permission", [])
            return {"type": kind, "properties": details}
        metadata = form.get("metadata")
        if kind != "form.created":
            return None
        if not isinstance(metadata, Mapping) or metadata.get("kind") != "question":
            raise ValueError("Unsupported V2 form kind; only question forms are supported")
        fields = self._fields(form.get("fields"))
        if len(self._pending) >= 1024:
            raise ValueError("V2 pending intervention capacity exceeded")
        tool = metadata.get("tool")
        details = {
            "id": request,
            "sessionID": session_id,
            "questions": [
                {
                    "header": field.get("title", ""),
                    "question": field.get("description", ""),
                    "options": deepcopy(field.get("options", [])),
                    "multiple": field["type"] == "multiselect",
                    "custom": field.get("custom", False),
                }
                for field in fields
            ],
        }
        if isinstance(tool, Mapping):
            details["tool"] = {"messageID": tool.get("messageID"), "callID": tool.get("id")}
        self._pending[key] = ("question", fields)
        return {"type": "question.asked", "properties": details}

    async def _bound(self, request_id: str, kind: str) -> tuple[str, list[dict[str, object]]]:
        resolution = await self._tracker.resolve_request(request_id)
        binding = resolution.binding
        if resolution.status != "active" or binding is None:
            raise ValueError("Intervention request is missing or expired")
        pending = self._pending.get((binding.session_id, request_id))
        if binding.interrupt_type != kind or pending is None or pending[0] != kind:
            raise ValueError("Intervention session or type does not match observed request")
        if (binding.session_id, request_id) in self._settled:
            raise ValueError("Intervention request is already settled")
        if self._validate_lease is not None:
            self._validate_lease(binding.session_id)
        return binding.session_id, pending[1]

    async def _request(
        self, request_id: str, kind: str, method: str, payload: dict[str, object] | None = None
    ) -> object:
        # Resolve before allocating a lock, so guessed IDs cannot grow this registry.
        await self._bound(request_id, kind)
        lock = self._locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            return await self._request_bound(request_id, kind, method, payload)

    async def _request_bound(
        self, request_id: str, kind: str, method: str, payload: dict[str, object] | None
    ) -> object:
        session, _ = await self._bound(request_id, kind)
        resource = "form" if kind == "question" else "permission"
        path = f"/api/session/{quote(session, safe='')}/{resource}/{quote(request_id, safe='')}"
        if method == "POST":
            path += "/reply"
        await self._client.get_server_info()
        async with self._client._request_budget.reserve(operation=path):
            # Recheck after asynchronous prerequisites, immediately before dispatch.
            await self._bound(request_id, kind)
            response = await self._client._client.request(
                method, path, headers=self._client._request_headers(), json=payload
            )
            response.raise_for_status()
            if method == "GET":
                data = self._client._unwrap_data(
                    self._client._decode_json_response(response, endpoint=path), endpoint=path
                )
                if (
                    not isinstance(data, Mapping)
                    or data.get("sessionID") != session
                    or data.get("id") != request_id
                ):
                    raise UpstreamContractError("V2 intervention response has mismatched ownership")
                return data
            if response.status_code != 204:
                raise UpstreamContractError("V2 intervention mutation must return HTTP 204")
        if (session, request_id) in self._pending:
            self._settled.add((session, request_id))
        await self._tracker.discard_request(request_id)
        return None

    async def get(self, request_id: str, *, interrupt_type: str) -> object:
        return await self._request(request_id, interrupt_type, "GET")

    async def permission_reply(
        self, request_id: str, *, reply: str, message: str | None = None
    ) -> bool:
        if reply not in {"once", "reject"}:
            raise ValueError("Restricted V2 permissions support only once or reject")
        if message is not None and not isinstance(message, str):
            raise ValueError("Permission message must be a string")
        payload: dict[str, object] = {"decision": reply}
        if message is not None:
            payload["message"] = message
        await self._request(request_id, "permission", "POST", payload)
        return True

    async def question_reply(self, request_id: str, *, answers: list[list[str]]) -> bool:
        _, fields = await self._bound(request_id, "question")
        if not isinstance(answers, list) or len(answers) != len(fields):
            raise ValueError("Answers must match the observed question field count")
        answer: dict[str, object] = {}
        for field, group in zip(fields, answers, strict=True):
            if not isinstance(group, list) or any(not isinstance(value, str) for value in group):
                raise ValueError("Answers must contain only arrays of strings")
            if field["type"] == "string" and len(group) > 1:
                raise ValueError("String question accepts a single answer")
            if not group:
                if field.get("required"):
                    raise ValueError("Missing required question answer")
                continue
            options = field.get("options")
            if isinstance(options, list) and not field.get("custom", False):
                values = {option["value"] for option in options}
                if any(value not in values for value in group):
                    raise ValueError("Answer is not a declared question option")
            if field.get("required") and field["type"] == "string" and not group[0]:
                raise ValueError("Missing required question answer")
            answer[cast(str, field["key"])] = group if field["type"] == "multiselect" else group[0]
        await self._request(request_id, "question", "POST", {"answer": answer})
        return True

    async def question_reject(self, request_id: str) -> bool:
        await self._request(request_id, "question", "DELETE")
        return True

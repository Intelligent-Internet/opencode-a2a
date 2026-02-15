from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import (
    A2AError,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONRPCError,
    JSONRPCRequest,
    Message,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from .opencode_client import OpencodeClient
from .text_parts import extract_text_from_parts

logger = logging.getLogger(__name__)

SESSION_QUERY_PAGINATION_DEFAULT_PAGE = 1
SESSION_QUERY_PAGINATION_DEFAULT_SIZE = 20
SESSION_QUERY_PAGINATION_MAX_SIZE = 100

ERR_SESSION_NOT_FOUND = -32001
ERR_UPSTREAM_UNREACHABLE = -32002
ERR_UPSTREAM_HTTP_ERROR = -32003


def _parse_positive_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        parsed = int(value)
    else:
        raise ValueError(f"{field} must be an integer")
    if parsed < 1:
        raise ValueError(f"{field} must be >= 1")
    return parsed


UNTITLED_SESSION_TITLE = "Untitled session"


def _extract_session_title(session: dict[str, Any]) -> str:
    candidates: list[Any] = [
        session.get("title"),
        session.get("name"),
    ]

    summary = session.get("summary")
    if isinstance(summary, dict):
        candidates.append(summary.get("title"))

    info = session.get("info")
    if isinstance(info, dict):
        info_summary = info.get("summary")
        if isinstance(info_summary, dict):
            candidates.append(info_summary.get("title"))

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Stable placeholder so downstream can always render a label.
    return UNTITLED_SESSION_TITLE


def _as_a2a_session_task(session: Any) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    raw_id = session.get("id") or session.get("session_id") or session.get("sessionId")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return None
    session_id = raw_id.strip()
    title = _extract_session_title(session)
    task = Task(
        id=session_id,
        context_id=session_id,
        # Model OpenCode sessions as completed A2A Tasks for stable downstream rendering.
        status=TaskStatus(state=TaskState.completed),
        metadata={"opencode": {"raw": session, "title": title}},
    )
    return task.model_dump(by_alias=True, exclude_none=True)


def _as_a2a_message(session_id: str, item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    info = item.get("info")
    raw_id = item.get("id") or item.get("message_id") or item.get("messageId")
    if raw_id is None and isinstance(info, dict):
        raw_id = info.get("id") or info.get("messageID") or info.get("messageId")
    message_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else str(uuid.uuid4())

    role_raw = item.get("role")
    if not isinstance(role_raw, str) and isinstance(info, dict):
        role_raw = info.get("role")
    role = Role.agent
    if isinstance(role_raw, str) and role_raw.strip().lower() == "user":
        role = Role.user

    text = item.get("text")
    if not isinstance(text, str):
        text = extract_text_from_parts(item.get("parts"))

    msg = Message(
        message_id=message_id,
        role=role,
        parts=[TextPart(text=text)],
        context_id=session_id,
        metadata={"opencode": {"raw": item, "session_id": session_id}},
    )
    return msg.model_dump(by_alias=True, exclude_none=True)


def _extract_raw_items(raw_result: Any, *, kind: str) -> list[Any]:
    """Extract list payloads from OpenCode responses.

    OpenCode serve does not guarantee a stable envelope. In the wild, list endpoints may return:
    - a top-level list
    - a dict with the list under a non-standard key (e.g. "sessions", "messages", "data")
    - a dict where there is exactly one list-valued field (plus metadata/pagination)

    We accept common variants and fall back to a conservative heuristic.
    """
    if isinstance(raw_result, list):
        return raw_result

    if not isinstance(raw_result, dict):
        return []

    if kind == "sessions":
        candidate_keys = ["items", "sessions", "data", "results"]
    elif kind == "messages":
        candidate_keys = ["items", "messages", "data", "results"]
    else:
        candidate_keys = ["items", "data", "results"]

    # Common case: { "items": [...] } or { "sessions": [...] } / { "messages": [...] }.
    for key in candidate_keys:
        value = raw_result.get(key)
        if isinstance(value, list):
            return value

    # Nested wrappers: { "data": { "items": [...] } } etc (one level deep).
    for key in ("data", "result"):
        nested = raw_result.get(key)
        if not isinstance(nested, dict):
            continue
        for nested_key in candidate_keys:
            value = nested.get(nested_key)
            if isinstance(value, list):
                return value

    # Heuristic: if there's exactly one list-valued field, treat it as the payload.
    list_keys = [k for k, v in raw_result.items() if isinstance(v, list)]
    if len(list_keys) == 1:
        guessed_key = list_keys[0]
        logger.debug(
            "OpenCode %s list payload missing standard keys=%s; using list field key=%s",
            kind,
            candidate_keys,
            guessed_key,
        )
        return raw_result[guessed_key]

    # Keep behavior stable: return [] and do not raise. Log keys for diagnosis (no content).
    logger.debug(
        "OpenCode %s list payload has no list field; type=dict keys=%s",
        kind,
        sorted(raw_result.keys()),
    )
    return []


class OpencodeSessionQueryJSONRPCApplication(A2AFastAPIApplication):
    """Extend A2A JSON-RPC endpoint with OpenCode session query methods.

    These methods are optional (declared via AgentCard.capabilities.extensions) and do
    not require additional private REST endpoints.
    """

    def __init__(
        self,
        *args: Any,
        opencode_client: OpencodeClient,
        methods: dict[str, str],
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._opencode_client = opencode_client
        self._method_list_sessions = methods["list_sessions"]
        self._method_get_session_messages = methods["get_session_messages"]

    async def _handle_requests(self, request: Request) -> Response:
        # Fast path: sniff method first then either handle here or delegate.
        request_id: str | int | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get("id")
                if request_id is not None and not isinstance(request_id, str | int):
                    request_id = None

            if not self._allowed_content_length(request):
                return self._generate_error_response(
                    request_id,
                    A2AError(root=InvalidRequestError(message="Payload too large")),
                )

            base_request = JSONRPCRequest.model_validate(body)
        except Exception:
            # Delegate to base implementation for consistent error handling.
            return await super()._handle_requests(request)

        if base_request.method not in {
            self._method_list_sessions,
            self._method_get_session_messages,
        }:
            return await super()._handle_requests(request)

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InvalidParamsError(message="params must be an object")),
            )

        query: dict[str, Any] = {}
        raw_query = params.get("query")
        if isinstance(raw_query, dict):
            query.update(raw_query)

        # Pagination contract: page/size only.
        if "cursor" in params or "limit" in params:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=(
                            "Only page/size pagination is supported (cursor/limit not supported)."
                        ),
                        data={
                            "type": "INVALID_PAGINATION_MODE",
                            "supported": ["page", "size"],
                            "unsupported": ["cursor", "limit"],
                        },
                    )
                ),
            )

        try:
            page = _parse_positive_int(params.get("page"), field="page")
            size = _parse_positive_int(params.get("size"), field="size")
        except ValueError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD"},
                    )
                ),
            )

        if size is not None and size > SESSION_QUERY_PAGINATION_MAX_SIZE:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=f"size must be <= {SESSION_QUERY_PAGINATION_MAX_SIZE}",
                        data={
                            "type": "SIZE_TOO_LARGE",
                            "field": "size",
                            "max_size": SESSION_QUERY_PAGINATION_MAX_SIZE,
                        },
                    )
                ),
            )

        if page is not None:
            query["page"] = page
        if size is not None:
            query["size"] = size

        session_id: str | None = None
        try:
            if base_request.method == self._method_list_sessions:
                raw_result = await self._opencode_client.list_sessions(params=query)
            else:
                session_id = params.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    return self._generate_error_response(
                        base_request.id,
                        A2AError(
                            root=InvalidParamsError(
                                message="Missing required params.session_id",
                                data={"type": "MISSING_FIELD", "field": "session_id"},
                            )
                        ),
                    )
                raw_result = await self._opencode_client.list_messages(session_id, params=query)
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404 and base_request.method == self._method_get_session_messages:
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_SESSION_NOT_FOUND,
                        message="Session not found",
                        data={"type": "SESSION_NOT_FOUND", "session_id": session_id},
                    ),
                )
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_HTTP_ERROR,
                    message="Upstream OpenCode error",
                    data={
                        "type": "UPSTREAM_HTTP_ERROR",
                        "upstream_status": upstream_status,
                    },
                ),
            )
        except httpx.HTTPError:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE"},
                ),
            )
        except Exception as exc:
            logger.exception("OpenCode session query JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        if base_request.method == self._method_list_sessions:
            raw_items = _extract_raw_items(raw_result, kind="sessions")
        else:
            raw_items = _extract_raw_items(raw_result, kind="messages")

        # Protocol: items are always arrays of A2A objects.
        # Task for sessions; Message for messages.
        if base_request.method == self._method_list_sessions:
            mapped: list[dict[str, Any]] = []
            for item in raw_items:
                task = _as_a2a_session_task(item)
                if task is not None:
                    mapped.append(task)
            items: list[dict[str, Any]] = mapped
        else:
            assert session_id is not None
            mapped = []
            for item in raw_items:
                message = _as_a2a_message(session_id, item)
                if message is not None:
                    mapped.append(message)
            items = mapped

        result = {
            "items": items,
            "pagination": {
                "mode": "page_size",
                "page": page,
                "size": size,
                "default_page": SESSION_QUERY_PAGINATION_DEFAULT_PAGE,
                "default_size": SESSION_QUERY_PAGINATION_DEFAULT_SIZE,
                "max_size": SESSION_QUERY_PAGINATION_MAX_SIZE,
            },
        }

        # Notifications (id omitted) should not yield a response.
        if base_request.id is None:
            return Response(status_code=204)

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": base_request.id,
                "result": result,
            }
        )

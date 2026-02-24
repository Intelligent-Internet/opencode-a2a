from __future__ import annotations

import logging
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

from .extension_contracts import (
    INTERRUPT_ERROR_BUSINESS_CODES,
    PROMPT_ASYNC_REQUEST_ALLOWED_FIELDS,
    SESSION_QUERY_ERROR_BUSINESS_CODES,
    SESSION_QUERY_PAGINATION_UNSUPPORTED,
)
from .opencode_client import OpencodeClient, UpstreamContractError
from .text_parts import extract_text_from_parts

logger = logging.getLogger(__name__)

ERR_SESSION_NOT_FOUND = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_NOT_FOUND"]
ERR_UPSTREAM_UNREACHABLE = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_UNREACHABLE"]
ERR_UPSTREAM_HTTP_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_HTTP_ERROR"]
ERR_INTERRUPT_NOT_FOUND = INTERRUPT_ERROR_BUSINESS_CODES["INTERRUPT_REQUEST_NOT_FOUND"]
ERR_UPSTREAM_PAYLOAD_ERROR = SESSION_QUERY_ERROR_BUSINESS_CODES["UPSTREAM_PAYLOAD_ERROR"]
SESSION_CONTEXT_PREFIX = "ctx:opencode-session:"


class _PromptAsyncValidationError(ValueError):
    def __init__(self, *, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _normalize_permission_reply(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("reply must be a string")
    normalized = value.strip().lower()
    if normalized == "once":
        return "once"
    if normalized == "always":
        return "always"
    if normalized == "reject":
        return "reject"
    raise ValueError("reply must be one of: once, always, reject")


def _parse_question_answers(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        raise ValueError("answers must be an array")
    if not value:
        return []
    answers: list[list[str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, list):
            raise ValueError(f"answers[{index}] must be an array of strings")
        parsed_group: list[str] = []
        for option in item:
            if not isinstance(option, str):
                raise ValueError(f"answers[{index}] must contain only strings")
            normalized = option.strip()
            if normalized:
                parsed_group.append(normalized)
        answers.append(parsed_group)
    return answers


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


def _raise_prompt_async_validation_error(*, field: str, message: str) -> None:
    raise _PromptAsyncValidationError(field=field, message=message)


def _validate_model_ref(value: Any, *, field: str) -> None:
    if not isinstance(value, dict):
        _raise_prompt_async_validation_error(field=field, message=f"{field} must be an object")
    provider = value.get("providerID")
    model = value.get("modelID")
    if not isinstance(provider, str) or not provider.strip():
        _raise_prompt_async_validation_error(
            field=f"{field}.providerID",
            message=f"{field}.providerID must be a non-empty string",
        )
    if not isinstance(model, str) or not model.strip():
        _raise_prompt_async_validation_error(
            field=f"{field}.modelID",
            message=f"{field}.modelID must be a non-empty string",
        )


def _validate_prompt_async_format(value: Any, *, field: str) -> None:
    if not isinstance(value, dict):
        _raise_prompt_async_validation_error(field=field, message=f"{field} must be an object")
    fmt_type = value.get("type")
    if fmt_type == "text":
        return
    if fmt_type == "json_schema":
        schema = value.get("schema")
        if not isinstance(schema, dict):
            _raise_prompt_async_validation_error(
                field=f"{field}.schema",
                message=f"{field}.schema must be an object for type=json_schema",
            )
        retry_count = value.get("retryCount")
        if retry_count is not None and (not isinstance(retry_count, int) or retry_count < 0):
            _raise_prompt_async_validation_error(
                field=f"{field}.retryCount",
                message=f"{field}.retryCount must be an integer >= 0",
            )
        return
    _raise_prompt_async_validation_error(
        field=f"{field}.type",
        message=f"{field}.type must be 'text' or 'json_schema'",
    )


def _validate_prompt_async_part(value: Any, *, field: str) -> None:
    if not isinstance(value, dict):
        _raise_prompt_async_validation_error(field=field, message=f"{field} must be an object")
    part_type = value.get("type")
    if not isinstance(part_type, str):
        _raise_prompt_async_validation_error(
            field=f"{field}.type",
            message=f"{field}.type must be a string",
        )
    if part_type == "text":
        if not isinstance(value.get("text"), str):
            _raise_prompt_async_validation_error(
                field=f"{field}.text",
                message=f"{field}.text must be a string",
            )
        return
    if part_type == "file":
        if not isinstance(value.get("mime"), str):
            _raise_prompt_async_validation_error(
                field=f"{field}.mime",
                message=f"{field}.mime must be a string",
            )
        if not isinstance(value.get("url"), str):
            _raise_prompt_async_validation_error(
                field=f"{field}.url",
                message=f"{field}.url must be a string",
            )
        return
    if part_type == "agent":
        if not isinstance(value.get("name"), str):
            _raise_prompt_async_validation_error(
                field=f"{field}.name",
                message=f"{field}.name must be a string",
            )
        return
    if part_type == "subtask":
        for key in ("prompt", "description", "agent"):
            if not isinstance(value.get(key), str):
                _raise_prompt_async_validation_error(
                    field=f"{field}.{key}",
                    message=f"{field}.{key} must be a string",
                )
        model = value.get("model")
        if model is not None:
            _validate_model_ref(model, field=f"{field}.model")
        command = value.get("command")
        if command is not None and not isinstance(command, str):
            _raise_prompt_async_validation_error(
                field=f"{field}.command",
                message=f"{field}.command must be a string",
            )
        return
    _raise_prompt_async_validation_error(
        field=f"{field}.type",
        message=f"{field}.type must be one of: text, file, agent, subtask",
    )


def _validate_prompt_async_request_payload(value: dict[str, Any]) -> None:
    allowed_fields = set(PROMPT_ASYNC_REQUEST_ALLOWED_FIELDS)
    unknown_fields = sorted(set(value) - allowed_fields)
    if unknown_fields:
        joined = ", ".join(f"request.{field}" for field in unknown_fields)
        _raise_prompt_async_validation_error(
            field="request",
            message=f"Unsupported fields: {joined}",
        )

    message_id = value.get("messageID")
    if message_id is not None:
        if not isinstance(message_id, str) or not message_id.startswith("msg"):
            _raise_prompt_async_validation_error(
                field="request.messageID",
                message="request.messageID must be a string starting with 'msg'",
            )

    model = value.get("model")
    if model is not None:
        _validate_model_ref(model, field="request.model")

    for key in ("agent", "system", "variant"):
        data = value.get(key)
        if data is not None and not isinstance(data, str):
            _raise_prompt_async_validation_error(
                field=f"request.{key}",
                message=f"request.{key} must be a string",
            )

    no_reply = value.get("noReply")
    if no_reply is not None and not isinstance(no_reply, bool):
        _raise_prompt_async_validation_error(
            field="request.noReply",
            message="request.noReply must be a boolean",
        )

    tools = value.get("tools")
    if tools is not None:
        if not isinstance(tools, dict):
            _raise_prompt_async_validation_error(
                field="request.tools",
                message="request.tools must be an object",
            )
        for tool_key, tool_value in tools.items():
            if not isinstance(tool_key, str):
                _raise_prompt_async_validation_error(
                    field="request.tools",
                    message="request.tools keys must be strings",
                )
            if not isinstance(tool_value, bool):
                _raise_prompt_async_validation_error(
                    field=f"request.tools.{tool_key}",
                    message=f"request.tools.{tool_key} must be a boolean",
                )

    fmt = value.get("format")
    if fmt is not None:
        _validate_prompt_async_format(fmt, field="request.format")

    parts = value.get("parts")
    if not isinstance(parts, list):
        _raise_prompt_async_validation_error(
            field="request.parts",
            message="request.parts must be an array",
        )
    for index, part in enumerate(parts):
        _validate_prompt_async_part(part, field=f"request.parts[{index}]")


def _extract_session_title(session: dict[str, Any]) -> str:
    title = session.get("title")
    if not isinstance(title, str):
        return ""
    return title.strip()


def _as_a2a_session_context_id(session_id: str) -> str:
    return f"{SESSION_CONTEXT_PREFIX}{session_id}"


def _as_a2a_session_task(session: Any) -> dict[str, Any] | None:
    if not isinstance(session, dict):
        return None
    raw_id = session.get("id")
    if not isinstance(raw_id, str):
        return None
    session_id = raw_id.strip()
    if not session_id:
        return None
    context_id = _as_a2a_session_context_id(session_id)
    title = _extract_session_title(session)
    task = Task(
        id=session_id,
        context_id=context_id,
        # Model OpenCode sessions as completed A2A Tasks for stable downstream rendering.
        status=TaskStatus(state=TaskState.completed),
        metadata={"opencode": {"session_id": session_id, "title": title}},
    )
    return task.model_dump(by_alias=True, exclude_none=True)


def _as_a2a_message(session_id: str, item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    info = item.get("info")
    if not isinstance(info, dict):
        return None
    raw_id = info.get("id")
    if not isinstance(raw_id, str):
        return None
    message_id = raw_id.strip()
    if not message_id:
        return None

    role_raw = info.get("role")
    role = Role.agent
    if isinstance(role_raw, str) and role_raw.strip().lower() == "user":
        role = Role.user

    text = extract_text_from_parts(item.get("parts"))

    context_id = _as_a2a_session_context_id(session_id)
    msg = Message(
        message_id=message_id,
        role=role,
        parts=[TextPart(text=text)],
        context_id=context_id,
        metadata={"opencode": {"session_id": session_id}},
    )
    return msg.model_dump(by_alias=True, exclude_none=True)


def _extract_raw_items(raw_result: Any, *, kind: str) -> list[Any]:
    """Extract list payloads from OpenCode responses."""
    if isinstance(raw_result, list):
        return raw_result
    raise ValueError(f"OpenCode {kind} payload must be an array; got {type(raw_result).__name__}")


class OpencodeSessionQueryJSONRPCApplication(A2AFastAPIApplication):
    """Extend A2A JSON-RPC endpoint with OpenCode session methods.

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
        self._method_prompt_async = methods["prompt_async"]
        self._method_reply_permission = methods["reply_permission"]
        self._method_reply_question = methods["reply_question"]
        self._method_reject_question = methods["reject_question"]

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

        session_query_methods = {
            self._method_list_sessions,
            self._method_get_session_messages,
        }
        session_control_methods = {self._method_prompt_async}
        interrupt_callback_methods = {
            self._method_reply_permission,
            self._method_reply_question,
            self._method_reject_question,
        }
        if (
            base_request.method
            not in session_query_methods | session_control_methods | interrupt_callback_methods
        ):
            return await super()._handle_requests(request)

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InvalidParamsError(message="params must be an object")),
            )

        if base_request.method in session_query_methods:
            return await self._handle_session_query_request(base_request, params)
        if base_request.method in session_control_methods:
            return await self._handle_session_control_request(base_request, params)
        return await self._handle_interrupt_callback_request(base_request, params, request=request)

    async def _handle_session_query_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
    ) -> Response:
        query: dict[str, Any] = {}
        raw_query = params.get("query")
        if raw_query is not None and not isinstance(raw_query, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="query must be an object",
                        data={"type": "INVALID_FIELD", "field": "query"},
                    )
                ),
            )
        if isinstance(raw_query, dict):
            query.update(raw_query)

        unsupported_pagination_fields = tuple(SESSION_QUERY_PAGINATION_UNSUPPORTED)
        if any(field in params for field in unsupported_pagination_fields):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Only limit pagination is supported",
                        data={
                            "type": "INVALID_PAGINATION_MODE",
                            "supported": ["limit"],
                            "unsupported": list(unsupported_pagination_fields),
                        },
                    )
                ),
            )
        if any(field in query for field in unsupported_pagination_fields):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Only limit pagination is supported",
                        data={
                            "type": "INVALID_PAGINATION_MODE",
                            "supported": ["limit"],
                            "unsupported": list(unsupported_pagination_fields),
                        },
                    )
                ),
            )

        if "limit" in params and "limit" in query and params["limit"] != query["limit"]:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="limit is ambiguous between params.limit and params.query.limit",
                        data={
                            "type": "INVALID_FIELD",
                            "field": "limit",
                        },
                    )
                ),
            )
        raw_limit = params.get("limit", query.get("limit"))
        try:
            limit = _parse_positive_int(raw_limit, field="limit")
        except ValueError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD", "field": "limit"},
                    )
                ),
            )
        if limit is not None:
            query["limit"] = limit

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

        try:
            if base_request.method == self._method_list_sessions:
                raw_items = _extract_raw_items(raw_result, kind="sessions")
            else:
                raw_items = _extract_raw_items(raw_result, kind="messages")
        except ValueError as exc:
            logger.warning("Upstream OpenCode payload mismatch: %s", exc)
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_PAYLOAD_ERROR,
                    message="Upstream OpenCode payload mismatch",
                    data={"type": "UPSTREAM_PAYLOAD_ERROR", "detail": str(exc)},
                ),
            )

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
        }

        # Notifications (id omitted) should not yield a response.
        if base_request.id is None:
            return Response(status_code=204)

        return self._jsonrpc_success_response(
            base_request.id,
            result,
        )

    async def _handle_session_control_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
    ) -> Response:
        allowed_fields = {"session_id", "request", "metadata"}
        unknown_fields = sorted(set(params) - allowed_fields)
        if unknown_fields:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=f"Unsupported fields: {', '.join(unknown_fields)}",
                        data={"type": "INVALID_FIELD", "fields": unknown_fields},
                    )
                ),
            )

        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Missing required params.session_id",
                        data={"type": "MISSING_FIELD", "field": "session_id"},
                    )
                ),
            )
        session_id = session_id.strip()

        raw_request = params.get("request")
        if raw_request is None:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Missing required params.request",
                        data={"type": "MISSING_FIELD", "field": "request"},
                    )
                ),
            )
        if not isinstance(raw_request, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="params.request must be an object",
                        data={"type": "INVALID_FIELD", "field": "request"},
                    )
                ),
            )

        try:
            _validate_prompt_async_request_payload(raw_request)
        except _PromptAsyncValidationError as exc:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=str(exc),
                        data={"type": "INVALID_FIELD", "field": exc.field},
                    )
                ),
            )

        metadata = params.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="metadata must be an object",
                        data={"type": "INVALID_FIELD", "field": "metadata"},
                    )
                ),
            )
        opencode_metadata: dict[str, Any] | None = None
        if isinstance(metadata, dict):
            unknown_metadata_fields = sorted(set(metadata) - {"opencode"})
            if unknown_metadata_fields:
                prefixed_fields = [f"metadata.{field}" for field in unknown_metadata_fields]
                return self._generate_error_response(
                    base_request.id,
                    A2AError(
                        root=InvalidParamsError(
                            message=f"Unsupported metadata fields: {', '.join(prefixed_fields)}",
                            data={"type": "INVALID_FIELD", "fields": prefixed_fields},
                        )
                    ),
                )
            raw_opencode_metadata = metadata.get("opencode")
            if raw_opencode_metadata is not None and not isinstance(raw_opencode_metadata, dict):
                return self._generate_error_response(
                    base_request.id,
                    A2AError(
                        root=InvalidParamsError(
                            message="metadata.opencode must be an object",
                            data={"type": "INVALID_FIELD", "field": "metadata.opencode"},
                        )
                    ),
                )
            if isinstance(raw_opencode_metadata, dict):
                opencode_metadata = raw_opencode_metadata

        directory = None
        if opencode_metadata is not None:
            directory = opencode_metadata.get("directory")
        if directory is not None and not isinstance(directory, str):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="metadata.opencode.directory must be a string",
                        data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
                    )
                ),
            )

        try:
            await self._opencode_client.session_prompt_async(
                session_id,
                request=dict(raw_request),
                directory=directory,
            )
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404:
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
                        "session_id": session_id,
                    },
                ),
            )
        except httpx.HTTPError:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE", "session_id": session_id},
                ),
            )
        except UpstreamContractError as exc:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_PAYLOAD_ERROR,
                    message="Upstream OpenCode payload mismatch",
                    data={
                        "type": "UPSTREAM_PAYLOAD_ERROR",
                        "detail": str(exc),
                        "session_id": session_id,
                    },
                ),
            )
        except Exception as exc:
            logger.exception("OpenCode session control JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        if base_request.id is None:
            return Response(status_code=204)
        return self._jsonrpc_success_response(
            base_request.id,
            {"ok": True, "session_id": session_id},
        )

    async def _handle_interrupt_callback_request(
        self,
        base_request: JSONRPCRequest,
        params: dict[str, Any],
        *,
        request: Request,
    ) -> Response:
        request_id = params.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="Missing required params.request_id",
                        data={"type": "MISSING_FIELD", "field": "request_id"},
                    )
                ),
            )
        request_id = request_id.strip()
        request_identity = getattr(request.state, "user_identity", None)
        metadata = params.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="metadata must be an object",
                        data={"type": "INVALID_FIELD", "field": "metadata"},
                    )
                ),
            )
        opencode_metadata: dict[str, Any] | None = None
        if isinstance(metadata, dict):
            unknown_metadata_fields = sorted(set(metadata) - {"opencode"})
            if unknown_metadata_fields:
                prefixed_fields = [f"metadata.{field}" for field in unknown_metadata_fields]
                return self._generate_error_response(
                    base_request.id,
                    A2AError(
                        root=InvalidParamsError(
                            message=f"Unsupported metadata fields: {', '.join(prefixed_fields)}",
                            data={"type": "INVALID_FIELD", "fields": prefixed_fields},
                        )
                    ),
                )
            raw_opencode_metadata = metadata.get("opencode")
            if raw_opencode_metadata is not None and not isinstance(raw_opencode_metadata, dict):
                return self._generate_error_response(
                    base_request.id,
                    A2AError(
                        root=InvalidParamsError(
                            message="metadata.opencode must be an object",
                            data={"type": "INVALID_FIELD", "field": "metadata.opencode"},
                        )
                    ),
                )
            if isinstance(raw_opencode_metadata, dict):
                opencode_metadata = raw_opencode_metadata

        directory = None
        if opencode_metadata is not None:
            directory = opencode_metadata.get("directory")
        if directory is not None and not isinstance(directory, str):
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message="metadata.opencode.directory must be a string",
                        data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
                    )
                ),
            )
        expected_interrupt_type = (
            "permission" if base_request.method == self._method_reply_permission else "question"
        )
        resolve_request = getattr(self._opencode_client, "resolve_interrupt_request", None)
        if callable(resolve_request):
            status, binding = resolve_request(request_id)
            if status != "active" or binding is None:
                error_type = (
                    "INTERRUPT_REQUEST_EXPIRED"
                    if status == "expired"
                    else "INTERRUPT_REQUEST_NOT_FOUND"
                )
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_INTERRUPT_NOT_FOUND,
                        message=(
                            "Interrupt request expired"
                            if status == "expired"
                            else "Interrupt request not found"
                        ),
                        data={"type": error_type, "request_id": request_id},
                    ),
                )
            if binding.interrupt_type != expected_interrupt_type:
                return self._generate_error_response(
                    base_request.id,
                    A2AError(
                        root=InvalidParamsError(
                            message=(
                                "Interrupt type mismatch: "
                                f"expected {expected_interrupt_type}, got {binding.interrupt_type}"
                            ),
                            data={
                                "type": "INTERRUPT_TYPE_MISMATCH",
                                "request_id": request_id,
                                "expected": expected_interrupt_type,
                                "actual": binding.interrupt_type,
                            },
                        )
                    ),
                )
            if (
                isinstance(request_identity, str)
                and request_identity
                and binding.identity
                and binding.identity != request_identity
            ):
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_INTERRUPT_NOT_FOUND,
                        message="Interrupt request not found",
                        data={
                            "type": "INTERRUPT_REQUEST_NOT_FOUND",
                            "request_id": request_id,
                        },
                    ),
                )
        else:
            resolve_session = getattr(self._opencode_client, "resolve_interrupt_session", None)
            if callable(resolve_session):
                if not resolve_session(request_id):
                    return self._generate_error_response(
                        base_request.id,
                        JSONRPCError(
                            code=ERR_INTERRUPT_NOT_FOUND,
                            message="Interrupt request not found",
                            data={
                                "type": "INTERRUPT_REQUEST_NOT_FOUND",
                                "request_id": request_id,
                            },
                        ),
                    )
        if base_request.method == self._method_reply_permission:
            allowed_fields = {"request_id", "reply", "message", "metadata"}
        elif base_request.method == self._method_reply_question:
            allowed_fields = {"request_id", "answers", "metadata"}
        else:
            allowed_fields = {"request_id", "metadata"}
        unknown_fields = sorted(set(params) - allowed_fields)
        if unknown_fields:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=f"Unsupported fields: {', '.join(unknown_fields)}",
                        data={"type": "INVALID_FIELD", "fields": unknown_fields},
                    )
                ),
            )

        try:
            if base_request.method == self._method_reply_permission:
                reply = _normalize_permission_reply(params.get("reply"))
                message = params.get("message")
                if message is not None and not isinstance(message, str):
                    raise ValueError("message must be a string")
                await self._opencode_client.permission_reply(
                    request_id,
                    reply=reply,
                    message=message,
                    directory=directory,
                )
                result: dict[str, Any] = {
                    "ok": True,
                    "request_id": request_id,
                }
            elif base_request.method == self._method_reply_question:
                answers = _parse_question_answers(params.get("answers"))
                await self._opencode_client.question_reply(
                    request_id,
                    answers=answers,
                    directory=directory,
                )
                result = {
                    "ok": True,
                    "request_id": request_id,
                }
            else:
                await self._opencode_client.question_reject(request_id, directory=directory)
                result = {
                    "ok": True,
                    "request_id": request_id,
                }
            discard_request = getattr(self._opencode_client, "discard_interrupt_request", None)
            if callable(discard_request):
                discard_request(request_id)
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
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 404:
                discard_request = getattr(self._opencode_client, "discard_interrupt_request", None)
                if callable(discard_request):
                    discard_request(request_id)
                return self._generate_error_response(
                    base_request.id,
                    JSONRPCError(
                        code=ERR_INTERRUPT_NOT_FOUND,
                        message="Interrupt request not found",
                        data={
                            "type": "INTERRUPT_REQUEST_NOT_FOUND",
                            "request_id": request_id,
                        },
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
                        "request_id": request_id,
                    },
                ),
            )
        except httpx.HTTPError:
            return self._generate_error_response(
                base_request.id,
                JSONRPCError(
                    code=ERR_UPSTREAM_UNREACHABLE,
                    message="Upstream OpenCode unreachable",
                    data={"type": "UPSTREAM_UNREACHABLE", "request_id": request_id},
                ),
            )
        except Exception as exc:
            logger.exception("OpenCode interrupt callback JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        if base_request.id is None:
            return Response(status_code=204)
        return self._jsonrpc_success_response(base_request.id, result)

    def _jsonrpc_success_response(self, request_id: str | int, result: Any) -> JSONResponse:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )

from __future__ import annotations

from typing import Any, cast

from a2a.types import Message, Part, Role, Task, TaskState, TaskStatus, TextPart

from ..contracts.extensions import (
    COMMAND_REQUEST_ALLOWED_FIELDS,
    PROMPT_ASYNC_REQUEST_ALLOWED_FIELDS,
    SHELL_REQUEST_ALLOWED_FIELDS,
)
from ..parts.text import extract_text_from_parts

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
    parts_list = cast(list[Any], parts)
    for index, part in enumerate(parts_list):
        _validate_prompt_async_part(part, field=f"request.parts[{index}]")


def _validate_command_part(value: Any, *, field: str) -> None:
    if not isinstance(value, dict):
        _raise_prompt_async_validation_error(field=field, message=f"{field} must be an object")
    part_type = value.get("type")
    if part_type != "file":
        _raise_prompt_async_validation_error(
            field=f"{field}.type",
            message=f"{field}.type must be 'file'",
        )
    for key in ("mime", "url"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            _raise_prompt_async_validation_error(
                field=f"{field}.{key}",
                message=f"{field}.{key} must be a non-empty string",
            )


def _validate_command_request_payload(value: dict[str, Any]) -> None:
    allowed_fields = set(COMMAND_REQUEST_ALLOWED_FIELDS)
    unknown_fields = sorted(set(value) - allowed_fields)
    if unknown_fields:
        joined = ", ".join(f"request.{field}" for field in unknown_fields)
        _raise_prompt_async_validation_error(
            field="request",
            message=f"Unsupported fields: {joined}",
        )

    for key in ("command", "arguments"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            _raise_prompt_async_validation_error(
                field=f"request.{key}",
                message=f"request.{key} must be a non-empty string",
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

    for key in ("agent", "variant"):
        data = value.get(key)
        if data is not None and not isinstance(data, str):
            _raise_prompt_async_validation_error(
                field=f"request.{key}",
                message=f"request.{key} must be a string",
            )

    parts = value.get("parts")
    if parts is not None:
        if not isinstance(parts, list):
            _raise_prompt_async_validation_error(
                field="request.parts",
                message="request.parts must be an array",
            )
        parts_list = cast(list[Any], parts)
        for index, part in enumerate(parts_list):
            _validate_command_part(part, field=f"request.parts[{index}]")


def _validate_shell_request_payload(value: dict[str, Any]) -> None:
    allowed_fields = set(SHELL_REQUEST_ALLOWED_FIELDS)
    unknown_fields = sorted(set(value) - allowed_fields)
    if unknown_fields:
        joined = ", ".join(f"request.{field}" for field in unknown_fields)
        _raise_prompt_async_validation_error(
            field="request",
            message=f"Unsupported fields: {joined}",
        )

    for key in ("agent", "command"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            _raise_prompt_async_validation_error(
                field=f"request.{key}",
                message=f"request.{key} must be a non-empty string",
            )

    model = value.get("model")
    if model is not None:
        _validate_model_ref(model, field="request.model")


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
        status=TaskStatus(state=TaskState.completed),
        metadata={"shared": {"session": {"id": session_id, "title": title}}},
    )
    return task.model_dump(by_alias=True, exclude_none=True)


def _as_a2a_message(session_id: str, item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    info: dict[str, Any]
    parts: Any = item.get("parts")
    raw_info = item.get("info")
    if isinstance(raw_info, dict):
        info = raw_info
    else:
        info = item

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

    text = extract_text_from_parts(parts if isinstance(parts, list) else [])

    context_id = _as_a2a_session_context_id(session_id)
    msg = Message(
        message_id=message_id,
        role=role,
        parts=[Part(root=TextPart(text=text))],
        context_id=context_id,
        metadata={"shared": {"session": {"id": session_id}}},
    )
    return msg.model_dump(by_alias=True, exclude_none=True)


def _extract_raw_items(raw_result: Any, *, kind: str) -> list[Any]:
    if isinstance(raw_result, list):
        return raw_result
    raise ValueError(f"OpenCode {kind} payload must be an array; got {type(raw_result).__name__}")


def _apply_session_query_limit(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if limit >= len(items):
        return items
    return items[:limit]


def _extract_provider_catalog(
    raw_result: Any,
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    if not isinstance(raw_result, dict):
        raise ValueError(
            f"OpenCode provider catalog payload must be an object; got {type(raw_result).__name__}"
        )

    raw_providers = raw_result.get("all")
    if not isinstance(raw_providers, list):
        raise ValueError("OpenCode provider catalog payload field 'all' must be an array")

    raw_defaults = raw_result.get("default")
    if not isinstance(raw_defaults, dict):
        raise ValueError("OpenCode provider catalog payload field 'default' must be an object")

    raw_connected = raw_result.get("connected")
    if not isinstance(raw_connected, list):
        raise ValueError("OpenCode provider catalog payload field 'connected' must be an array")

    providers: list[dict[str, Any]] = []
    for item in raw_providers:
        if not isinstance(item, dict):
            raise ValueError("OpenCode provider catalog items must be objects")
        providers.append(item)

    default_by_provider: dict[str, str] = {}
    for key, value in raw_defaults.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("OpenCode provider catalog default entries must be string:string")
        provider_id = key.strip()
        model_id = value.strip()
        if provider_id and model_id:
            default_by_provider[provider_id] = model_id

    connected: list[str] = []
    for item in raw_connected:
        if not isinstance(item, str):
            raise ValueError("OpenCode provider catalog connected entries must be strings")
        provider_id = item.strip()
        if provider_id:
            connected.append(provider_id)

    return providers, default_by_provider, connected


def _normalize_provider_summaries(
    raw_providers: list[dict[str, Any]],
    *,
    default_by_provider: dict[str, str],
    connected: list[str],
) -> list[dict[str, Any]]:
    connected_set = set(connected)
    items: list[dict[str, Any]] = []
    for provider in raw_providers:
        raw_id = provider.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        provider_id = raw_id.strip()
        raw_models = provider.get("models")
        model_count = len(raw_models) if isinstance(raw_models, dict) else 0
        item: dict[str, Any] = {
            "provider_id": provider_id,
            "name": provider.get("name") if isinstance(provider.get("name"), str) else provider_id,
            "connected": provider_id in connected_set,
            "model_count": model_count,
        }
        source = provider.get("source")
        if isinstance(source, str) and source.strip():
            item["source"] = source.strip()
        default_model_id = default_by_provider.get(provider_id)
        if default_model_id:
            item["default_model_id"] = default_model_id
        items.append(item)
    return items


def _normalize_model_summaries(
    raw_providers: list[dict[str, Any]],
    *,
    default_by_provider: dict[str, str],
    connected: list[str],
    provider_id: str | None = None,
) -> list[dict[str, Any]]:
    connected_set = set(connected)
    items: list[dict[str, Any]] = []
    for provider in raw_providers:
        raw_id = provider.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        current_provider_id = raw_id.strip()
        if provider_id is not None and current_provider_id != provider_id:
            continue
        raw_models = provider.get("models")
        if not isinstance(raw_models, dict):
            continue
        default_model_id = default_by_provider.get(current_provider_id)
        for raw_model_id, raw_model in raw_models.items():
            if not isinstance(raw_model_id, str) or not raw_model_id.strip():
                continue
            if not isinstance(raw_model, dict):
                continue
            model_id = raw_model_id.strip()
            item: dict[str, Any] = {
                "provider_id": current_provider_id,
                "model_id": model_id,
                "name": raw_model.get("name")
                if isinstance(raw_model.get("name"), str)
                else model_id,
                "default": model_id == default_model_id,
                "connected": current_provider_id in connected_set,
            }
            status = raw_model.get("status")
            if isinstance(status, str) and status.strip():
                item["status"] = status.strip()
            limit = raw_model.get("limit")
            if isinstance(limit, dict):
                context_window = limit.get("context")
                max_output_tokens = limit.get("output")
                if isinstance(context_window, int):
                    item["context_window"] = context_window
                if isinstance(max_output_tokens, int):
                    item["max_output_tokens"] = max_output_tokens
            capabilities = raw_model.get("capabilities")
            if isinstance(capabilities, dict):
                for source_key, target_key in (
                    ("reasoning", "supports_reasoning"),
                    ("toolcall", "supports_tool_call"),
                    ("attachment", "supports_attachments"),
                ):
                    value = capabilities.get(source_key)
                    if isinstance(value, bool):
                        item[target_key] = value
            items.append(item)
    return items

import pytest

from opencode_a2a_server.jsonrpc.application import (
    _extract_provider_catalog,
    _normalize_model_summaries,
    _normalize_permission_reply,
    _normalize_provider_summaries,
    _parse_question_answers,
    _PromptAsyncValidationError,
    _validate_command_request_payload,
    _validate_prompt_async_format,
    _validate_prompt_async_part,
    _validate_shell_request_payload,
)


def test_normalize_permission_reply_accepts_supported_values() -> None:
    assert _normalize_permission_reply(" Once ") == "once"
    assert _normalize_permission_reply("ALWAYS") == "always"
    assert _normalize_permission_reply("reject") == "reject"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "reply must be a string"),
        ("later", "reply must be one of: once, always, reject"),
    ],
)
def test_normalize_permission_reply_rejects_invalid_values(value, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _normalize_permission_reply(value)


def test_parse_question_answers_strips_empty_values() -> None:
    assert _parse_question_answers([[" yes ", "", "no"], []]) == [["yes", "no"], []]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("invalid", "answers must be an array"),
        ([1], "answers\\[0\\] must be an array of strings"),
        ([["ok", 1]], "answers\\[0\\] must contain only strings"),
    ],
)
def test_parse_question_answers_rejects_invalid_shapes(value, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _parse_question_answers(value)


def test_validate_prompt_async_format_accepts_text_and_json_schema() -> None:
    _validate_prompt_async_format({"type": "text"}, field="request.format")
    _validate_prompt_async_format(
        {"type": "json_schema", "schema": {"type": "object"}, "retryCount": 2},
        field="request.format",
    )


@pytest.mark.parametrize(
    ("value", "field", "message"),
    [
        ("invalid", "request.format", "request.format must be an object"),
        (
            {"type": "json_schema", "schema": "bad"},
            "request.format.schema",
            "request.format.schema must be an object for type=json_schema",
        ),
        (
            {"type": "json_schema", "schema": {}, "retryCount": -1},
            "request.format.retryCount",
            "request.format.retryCount must be an integer >= 0",
        ),
        (
            {"type": "xml"},
            "request.format.type",
            "request.format.type must be 'text' or 'json_schema'",
        ),
    ],
)
def test_validate_prompt_async_format_rejects_invalid_values(
    value,
    field: str,
    message: str,
) -> None:
    with pytest.raises(_PromptAsyncValidationError, match=message) as exc_info:
        _validate_prompt_async_format(value, field="request.format")

    assert exc_info.value.field == field


def test_validate_prompt_async_part_accepts_subtask_with_optional_fields() -> None:
    _validate_prompt_async_part(
        {
            "type": "subtask",
            "prompt": "continue",
            "description": "do the next step",
            "agent": "planner",
            "model": {"providerID": "google", "modelID": "gemini"},
            "command": "/review",
        },
        field="request.parts[0]",
    )


@pytest.mark.parametrize(
    ("value", "field", "message"),
    [
        (
            {"type": "file", "mime": "text/plain"},
            "request.parts[0].url",
            "request.parts\\[0\\].url must be a string",
        ),
        (
            {"type": "agent"},
            "request.parts[0].name",
            "request.parts\\[0\\].name must be a string",
        ),
        (
            {"type": "subtask", "prompt": "p", "description": "d", "agent": "a", "command": 1},
            "request.parts[0].command",
            "request.parts\\[0\\].command must be a string",
        ),
        (
            {"type": "unknown"},
            "request.parts[0].type",
            "request.parts\\[0\\].type must be one of: text, file, agent, subtask",
        ),
    ],
)
def test_validate_prompt_async_part_rejects_invalid_values(value, field: str, message: str) -> None:
    with pytest.raises(_PromptAsyncValidationError, match=message) as exc_info:
        _validate_prompt_async_part(value, field="request.parts[0]")

    assert exc_info.value.field == field


def test_validate_command_and_shell_payload_helpers_reject_invalid_input() -> None:
    with pytest.raises(_PromptAsyncValidationError, match="Unsupported fields: request.extra"):
        _validate_command_request_payload(
            {"command": "/review", "arguments": "security", "extra": True}
        )

    with pytest.raises(
        _PromptAsyncValidationError, match="request.parts\\[0\\].type must be 'file'"
    ):
        _validate_command_request_payload(
            {"command": "/review", "arguments": "security", "parts": [{"type": "text"}]}
        )

    with pytest.raises(
        _PromptAsyncValidationError, match="request.command must be a non-empty string"
    ):
        _validate_shell_request_payload({"agent": "planner", "command": ""})


def test_extract_provider_catalog_normalizes_defaults_and_connected() -> None:
    providers, defaults, connected = _extract_provider_catalog(
        {
            "all": [{"id": "openai", "models": {"gpt-4.1": {"name": "GPT-4.1"}}}],
            "default": {" openai ": " gpt-4.1 ", "": "ignored"},
            "connected": [" openai ", "", "vertex"],
        }
    )

    assert providers == [{"id": "openai", "models": {"gpt-4.1": {"name": "GPT-4.1"}}}]
    assert defaults == {"openai": "gpt-4.1"}
    assert connected == ["openai", "vertex"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("bad", "payload must be an object"),
        ({"all": "bad", "default": {}, "connected": []}, "field 'all' must be an array"),
        ({"all": [], "default": [], "connected": []}, "field 'default' must be an object"),
        ({"all": [], "default": {}, "connected": {}}, "field 'connected' must be an array"),
    ],
)
def test_extract_provider_catalog_rejects_invalid_top_level_shapes(payload, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _extract_provider_catalog(payload)


def test_normalize_provider_summaries_skips_invalid_entries_and_sets_metadata() -> None:
    items = _normalize_provider_summaries(
        [
            {"id": " openai ", "name": "OpenAI", "models": {"gpt-4.1": {}}, "source": " cloud "},
            {"id": "", "models": {}},
            {"id": "vertex", "models": []},
        ],
        default_by_provider={"openai": "gpt-4.1"},
        connected=["vertex"],
    )

    assert items == [
        {
            "provider_id": "openai",
            "name": "OpenAI",
            "connected": False,
            "model_count": 1,
            "source": "cloud",
            "default_model_id": "gpt-4.1",
        },
        {
            "provider_id": "vertex",
            "name": "vertex",
            "connected": True,
            "model_count": 0,
        },
    ]


def test_normalize_model_summaries_extracts_capabilities_and_limits() -> None:
    items = _normalize_model_summaries(
        [
            {
                "id": "openai",
                "models": {
                    "gpt-4.1": {
                        "name": "GPT-4.1",
                        "status": " stable ",
                        "limit": {"context": 128000, "output": 8192},
                        "capabilities": {
                            "reasoning": True,
                            "toolcall": False,
                            "attachment": True,
                        },
                    },
                    "": {"name": "skip"},
                },
            },
            {"id": "vertex", "models": {"gemini": "skip"}},
        ],
        default_by_provider={"openai": "gpt-4.1"},
        connected=["openai"],
        provider_id="openai",
    )

    assert items == [
        {
            "provider_id": "openai",
            "model_id": "gpt-4.1",
            "name": "GPT-4.1",
            "default": True,
            "connected": True,
            "status": "stable",
            "context_window": 128000,
            "max_output_tokens": 8192,
            "supports_reasoning": True,
            "supports_tool_call": False,
            "supports_attachments": True,
        }
    ]

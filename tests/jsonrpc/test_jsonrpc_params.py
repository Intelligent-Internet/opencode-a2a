import pytest

from opencode_a2a.contracts.extensions import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    SESSION_QUERY_PAGINATION_UNSUPPORTED,
)
from opencode_a2a.jsonrpc.params import (
    JsonRpcParamsValidationError,
    parse_get_session_messages_params,
    parse_list_sessions_params,
)


def test_parse_list_sessions_params_applies_default_limit() -> None:
    assert parse_list_sessions_params({}) == {"limit": SESSION_QUERY_DEFAULT_LIMIT}


def test_parse_list_sessions_params_accepts_equivalent_query_and_top_level_limit() -> None:
    assert parse_list_sessions_params({"limit": "10", "query": {"limit": 10, "tag": "ops"}}) == {
        "tag": "ops",
        "limit": 10,
    }


def test_parse_list_sessions_params_rejects_limit_above_max() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_list_sessions_params({"limit": SESSION_QUERY_MAX_LIMIT + 1})

    assert str(exc_info.value) == f"limit must be <= {SESSION_QUERY_MAX_LIMIT}"
    assert exc_info.value.data == {
        "type": "INVALID_FIELD",
        "field": "limit",
        "max": SESSION_QUERY_MAX_LIMIT,
    }


def test_parse_get_session_messages_params_requires_session_id() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_get_session_messages_params({})

    assert str(exc_info.value) == "Missing required params.session_id"
    assert exc_info.value.data == {"type": "MISSING_FIELD", "field": "session_id"}


def test_parse_get_session_messages_params_applies_default_limit() -> None:
    session_id, query = parse_get_session_messages_params({"session_id": "s-1"})

    assert session_id == "s-1"
    assert query == {"limit": SESSION_QUERY_DEFAULT_LIMIT}


def test_parse_get_session_messages_params_rejects_ambiguous_limit() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_get_session_messages_params({"session_id": "s-1", "limit": 5, "query": {"limit": 6}})

    assert str(exc_info.value) == "limit is ambiguous between params.limit and params.query.limit"
    assert exc_info.value.data == {"type": "INVALID_FIELD", "field": "limit"}


def test_parse_list_sessions_params_rejects_non_object_query() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_list_sessions_params({"query": "invalid"})

    assert str(exc_info.value) == "query must be an object"
    assert exc_info.value.data == {"type": "INVALID_FIELD", "field": "query"}


def test_parse_list_sessions_params_rejects_unsupported_pagination_fields() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_list_sessions_params({"cursor": "next-page"})

    assert str(exc_info.value) == "Only limit pagination is supported"
    assert exc_info.value.data == {
        "type": "INVALID_PAGINATION_MODE",
        "supported": ["limit"],
        "unsupported": list(SESSION_QUERY_PAGINATION_UNSUPPORTED),
    }


def test_parse_list_sessions_params_rejects_boolean_limit() -> None:
    with pytest.raises(JsonRpcParamsValidationError) as exc_info:
        parse_list_sessions_params({"limit": True})

    assert str(exc_info.value) == "limit must be an integer"
    assert exc_info.value.data == {"type": "INVALID_FIELD", "field": "limit"}


def test_parse_get_session_messages_params_trims_session_id() -> None:
    session_id, query = parse_get_session_messages_params({"session_id": "  s-1  "})

    assert session_id == "s-1"
    assert query == {"limit": SESSION_QUERY_DEFAULT_LIMIT}

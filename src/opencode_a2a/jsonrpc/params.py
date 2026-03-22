from __future__ import annotations

from typing import Any

from ..contracts.extensions import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    SESSION_QUERY_PAGINATION_UNSUPPORTED,
)


class JsonRpcParamsValidationError(ValueError):
    def __init__(self, *, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


def _parse_positive_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise JsonRpcParamsValidationError(
            message=f"{field} must be an integer",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise JsonRpcParamsValidationError(
                message=f"{field} must be an integer",
                data={"type": "INVALID_FIELD", "field": field},
            ) from exc
    else:
        raise JsonRpcParamsValidationError(
            message=f"{field} must be an integer",
            data={"type": "INVALID_FIELD", "field": field},
        )
    if parsed < 1:
        raise JsonRpcParamsValidationError(
            message=f"{field} must be >= 1",
            data={"type": "INVALID_FIELD", "field": field},
        )
    return parsed


def _parse_query_object(params: dict[str, Any]) -> dict[str, Any]:
    raw_query = params.get("query")
    if raw_query is None:
        return {}
    if not isinstance(raw_query, dict):
        raise JsonRpcParamsValidationError(
            message="query must be an object",
            data={"type": "INVALID_FIELD", "field": "query"},
        )
    return dict(raw_query)


def _validate_pagination_fields(params: dict[str, Any], query: dict[str, Any]) -> None:
    unsupported_fields = tuple(SESSION_QUERY_PAGINATION_UNSUPPORTED)
    if any(field in params for field in unsupported_fields) or any(
        field in query for field in unsupported_fields
    ):
        raise JsonRpcParamsValidationError(
            message="Only limit pagination is supported",
            data={
                "type": "INVALID_PAGINATION_MODE",
                "supported": ["limit"],
                "unsupported": list(unsupported_fields),
            },
        )


def _normalize_session_query_limit(
    *,
    params: dict[str, Any],
    query: dict[str, Any],
) -> dict[str, Any]:
    top_level_limit = _parse_positive_int(params.get("limit"), field="limit")
    query_limit = _parse_positive_int(query.get("limit"), field="limit")
    if top_level_limit is not None and query_limit is not None and top_level_limit != query_limit:
        raise JsonRpcParamsValidationError(
            message="limit is ambiguous between params.limit and params.query.limit",
            data={"type": "INVALID_FIELD", "field": "limit"},
        )

    normalized_limit = top_level_limit if top_level_limit is not None else query_limit
    if normalized_limit is None:
        normalized_limit = SESSION_QUERY_DEFAULT_LIMIT
    elif normalized_limit > SESSION_QUERY_MAX_LIMIT:
        raise JsonRpcParamsValidationError(
            message=f"limit must be <= {SESSION_QUERY_MAX_LIMIT}",
            data={
                "type": "INVALID_FIELD",
                "field": "limit",
                "max": SESSION_QUERY_MAX_LIMIT,
            },
        )

    normalized_query = dict(query)
    normalized_query["limit"] = normalized_limit
    return normalized_query


def parse_list_sessions_params(params: dict[str, Any]) -> dict[str, Any]:
    query = _parse_query_object(params)
    _validate_pagination_fields(params, query)
    return _normalize_session_query_limit(params=params, query=query)


def parse_get_session_messages_params(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_session_id = params.get("session_id")
    if not isinstance(raw_session_id, str) or not raw_session_id.strip():
        raise JsonRpcParamsValidationError(
            message="Missing required params.session_id",
            data={"type": "MISSING_FIELD", "field": "session_id"},
        )

    query = _parse_query_object(params)
    _validate_pagination_fields(params, query)
    return raw_session_id.strip(), _normalize_session_query_limit(params=params, query=query)

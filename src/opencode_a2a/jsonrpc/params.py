from __future__ import annotations

from functools import partial
from typing import Any

from ..contracts.extensions import (
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_MAX_LIMIT,
    SESSION_QUERY_PAGINATION_UNSUPPORTED,
)
from ..parsing import (
    parse_bool_field as parse_shared_bool_field,
)
from ..parsing import (
    parse_int_field as parse_shared_int_field,
)
from ..parsing import (
    parse_string_field as parse_shared_string_field,
)


class JsonRpcParamsValidationError(ValueError):
    def __init__(self, *, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


def _validation_error(field: str, message: str) -> JsonRpcParamsValidationError:
    return JsonRpcParamsValidationError(
        message=message,
        data={"type": "INVALID_FIELD", "field": field},
    )


_parse_required_positive_int = partial(
    parse_shared_int_field,
    error_factory=_validation_error,
    minimum=1,
)
_parse_non_negative_int = partial(
    parse_shared_int_field,
    error_factory=_validation_error,
    minimum=0,
)
_parse_string_field = partial(
    parse_shared_string_field,
    error_factory=_validation_error,
)
_parse_bool_field = partial(
    parse_shared_bool_field,
    error_factory=_validation_error,
)


def _reject_nested_query_params(params: dict[str, Any]) -> None:
    if "query" not in params:
        return
    raise JsonRpcParamsValidationError(
        message="query is not supported; use top-level params",
        data={"type": "INVALID_FIELD", "field": "query"},
    )


def _validate_pagination_fields(params: dict[str, Any]) -> None:
    unsupported_fields = tuple(SESSION_QUERY_PAGINATION_UNSUPPORTED)
    if any(field in params for field in unsupported_fields):
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
    limit: Any,
) -> dict[str, Any]:
    normalized_limit = _parse_required_positive_int(limit, field="limit")
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

    return {"limit": normalized_limit}


def parse_list_sessions_params(params: dict[str, Any]) -> dict[str, Any]:
    _reject_nested_query_params(params)
    _validate_pagination_fields(params)
    normalized_query = _normalize_session_query_limit(limit=params.get("limit"))
    directory = _parse_string_field(params.get("directory"), field="directory")
    roots = _parse_bool_field(params.get("roots"), field="roots")
    start = _parse_non_negative_int(params.get("start"), field="start")
    search = _parse_string_field(params.get("search"), field="search")

    if directory is not None:
        normalized_query["directory"] = directory
    if roots is not None:
        normalized_query["roots"] = roots
    if start is not None:
        normalized_query["start"] = start
    if search is not None:
        normalized_query["search"] = search
    return normalized_query


def parse_get_session_messages_params(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw_session_id = params.get("session_id")
    if not isinstance(raw_session_id, str) or not raw_session_id.strip():
        raise JsonRpcParamsValidationError(
            message="Missing required params.session_id",
            data={"type": "MISSING_FIELD", "field": "session_id"},
        )

    _reject_nested_query_params(params)
    _validate_pagination_fields(params)
    normalized_query = _normalize_session_query_limit(limit=params.get("limit"))
    before = _parse_string_field(params.get("before"), field="before")
    if before is not None:
        normalized_query["before"] = before
    return raw_session_id.strip(), normalized_query

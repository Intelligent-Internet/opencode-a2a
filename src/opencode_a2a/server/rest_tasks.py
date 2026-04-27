from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from a2a.server.tasks.task_store import TaskStore
from a2a.types import Task, TaskState
from fastapi import Request
from fastapi.responses import JSONResponse

from ..a2a_utils import proto_to_dict
from ..jsonrpc.error_responses import build_http_error_body
from ..output_modes import (
    apply_accepted_output_modes,
    extract_accepted_output_modes_from_metadata,
)
from ..parsing import (
    parse_bool_field as parse_shared_bool_field,
)
from ..parsing import (
    parse_int_field as parse_shared_int_field,
)
from ..parsing import (
    parse_timestamp_field as parse_shared_timestamp_field,
)
from .task_store import TaskStoreOperationError, list_stored_tasks

logger = logging.getLogger(__name__)
_DEFAULT_LIST_TASKS_PAGE_SIZE = 50
_MAX_LIST_TASKS_PAGE_SIZE = 100
_MIN_LIST_TASKS_PAGE_SIZE = 1


@dataclass(frozen=True)
class _TaskCursor:
    task_id: str
    timestamp: datetime


@dataclass(frozen=True)
class _ListTasksQuery:
    cursor: _TaskCursor | None
    context_id: str | None
    include_artifacts: bool
    history_length: int
    requested_page_size: int
    status: TaskState | None
    status_timestamp_after: datetime | None


class _ListTasksValidationError(ValueError):
    def __init__(self, *, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def _validation_error(field: str, message: str) -> _ListTasksValidationError:
    return _ListTasksValidationError(field=field, message=message)


def build_list_tasks_route(
    *,
    task_store: TaskStore,
    default_protocol_version: str,
):
    async def list_tasks_route(request: Request) -> JSONResponse:
        protocol_version = getattr(
            request.state,
            "a2a_protocol_version",
            default_protocol_version,
        )
        try:
            query = _parse_list_tasks_query(request)
            tasks = await list_stored_tasks(task_store)
        except _ListTasksValidationError as error:
            return _invalid_argument_response(
                field=error.field,
                message=error.message,
                protocol_version=protocol_version,
            )
        except TaskStoreOperationError as error:
            return JSONResponse(
                build_http_error_body(
                    protocol_version=protocol_version,
                    status_code=500,
                    status="INTERNAL",
                    message="Task store unavailable while listing tasks.",
                    legacy_payload={
                        "error": "Task store unavailable while listing tasks.",
                        "operation": error.operation,
                    },
                    reason="TASK_STORE_UNAVAILABLE",
                    metadata={"operation": error.operation},
                ),
                status_code=500,
            )

        filtered_tasks = _filter_tasks(tasks, query=query)
        total_size = len(filtered_tasks)
        paged_tasks = _apply_cursor(filtered_tasks, cursor=query.cursor)
        page_tasks = paged_tasks[: query.requested_page_size]
        next_page_token = ""
        if len(paged_tasks) > len(page_tasks) and page_tasks:
            next_page_token = _encode_page_token(page_tasks[-1])

        return JSONResponse(
            {
                "tasks": [
                    _serialize_task(
                        task,
                        history_length=query.history_length,
                        include_artifacts=query.include_artifacts,
                    )
                    for task in page_tasks
                ],
                "nextPageToken": next_page_token,
                "pageSize": len(page_tasks),
                "totalSize": total_size,
            }
        )

    return list_tasks_route


def _filter_tasks(tasks: list[Task], *, query: _ListTasksQuery) -> list[Task]:
    filtered = tasks

    if query.context_id is not None:
        filtered = [task for task in filtered if task.context_id == query.context_id]

    if query.status is not None:
        filtered = [task for task in filtered if task.status.state == query.status]

    if query.status_timestamp_after is not None:
        filtered = [
            task for task in filtered if _task_sort_key(task)[0] >= query.status_timestamp_after
        ]

    return sorted(
        filtered,
        key=_task_sort_key,
        reverse=True,
    )


def _apply_cursor(tasks: list[Task], *, cursor: _TaskCursor | None) -> list[Task]:
    if cursor is None:
        return tasks
    return [task for task in tasks if _task_sort_key(task) < _cursor_sort_key(cursor)]


def _serialize_task(
    task: Task,
    *,
    history_length: int,
    include_artifacts: bool,
) -> dict:
    negotiated = apply_accepted_output_modes(
        task,
        extract_accepted_output_modes_from_metadata(task.metadata),
    )
    if isinstance(negotiated, Task):
        task = negotiated

    payload = proto_to_dict(task)

    history = payload.get("history")
    if history_length <= 0:
        payload.pop("history", None)
    elif isinstance(history, list):
        payload["history"] = history[-history_length:]

    if not include_artifacts:
        payload.pop("artifacts", None)

    return payload


def _parse_list_tasks_query(request: Request) -> _ListTasksQuery:
    page_size_value = request.query_params.get("pageSize")
    if page_size_value is None:
        requested_page_size = _DEFAULT_LIST_TASKS_PAGE_SIZE
    else:
        requested_page_size = _parse_int(page_size_value, field="pageSize")
        if not (_MIN_LIST_TASKS_PAGE_SIZE <= requested_page_size <= _MAX_LIST_TASKS_PAGE_SIZE):
            raise _ListTasksValidationError(
                field="pageSize",
                message="pageSize must be between 1 and 100.",
            )

    history_length_value = request.query_params.get("historyLength")
    if history_length_value is None:
        history_length = 0
    else:
        history_length = _parse_int(history_length_value, field="historyLength")
        if history_length < 0:
            raise _ListTasksValidationError(
                field="historyLength",
                message="historyLength must be greater than or equal to 0.",
            )

    include_artifacts = _parse_bool(
        request.query_params.get("includeArtifacts"),
        field="includeArtifacts",
        default=False,
    )
    cursor = _decode_page_token(request.query_params.get("pageToken"))

    status_value = request.query_params.get("status")
    status = None
    if status_value is not None:
        try:
            normalized_status = status_value.strip().upper()
            if normalized_status and not normalized_status.startswith("TASK_STATE_"):
                normalized_status = f"TASK_STATE_{normalized_status}"
            status = TaskState.Value(normalized_status)
        except ValueError as exc:
            raise _ListTasksValidationError(
                field="status",
                message=f"Unsupported task status {status_value!r}.",
            ) from exc

    status_timestamp_after = None
    status_timestamp_after_value = request.query_params.get("statusTimestampAfter")
    if status_timestamp_after_value is not None:
        status_timestamp_after = _parse_timestamp(
            status_timestamp_after_value,
            field="statusTimestampAfter",
        )

    return _ListTasksQuery(
        cursor=cursor,
        context_id=request.query_params.get("contextId"),
        include_artifacts=include_artifacts,
        history_length=history_length,
        requested_page_size=requested_page_size,
        status=status,
        status_timestamp_after=status_timestamp_after,
    )


def _parse_int(raw_value: str, *, field: str) -> int:
    parsed = parse_shared_int_field(
        raw_value,
        field=field,
        error_factory=lambda error_field, _message: _validation_error(
            error_field,
            f"{error_field} must be an integer.",
        ),
    )
    assert parsed is not None
    return parsed


def _parse_bool(raw_value: str | None, *, field: str, default: bool) -> bool:
    parsed = parse_shared_bool_field(
        raw_value,
        field=field,
        error_factory=lambda error_field, _message: _validation_error(
            error_field,
            f"{error_field} must be a boolean.",
        ),
        true_values=("true", "1"),
        false_values=("false", "0"),
    )
    return default if parsed is None else parsed


def _parse_timestamp(raw_value: str, *, field: str) -> datetime:
    return parse_shared_timestamp_field(
        raw_value,
        field=field,
        error_factory=_validation_error,
    )


def _task_status_timestamp(task: Task) -> datetime:
    if not task.status.HasField("timestamp"):
        return datetime.min.replace(tzinfo=UTC)
    timestamp = task.status.timestamp
    if hasattr(timestamp, "ToDatetime"):
        value = cast(datetime, timestamp.ToDatetime())
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    try:
        return _parse_timestamp(timestamp, field="status.timestamp")
    except _ListTasksValidationError:
        logger.warning(
            "Ignoring invalid task status timestamp while listing tasks task_id=%s timestamp=%r",
            task.id,
            timestamp,
        )
        return datetime.min.replace(tzinfo=UTC)


def _task_sort_key(task: Task) -> tuple[datetime, str]:
    return (_task_status_timestamp(task), task.id)


def _cursor_sort_key(cursor: _TaskCursor) -> tuple[datetime, str]:
    return (cursor.timestamp, cursor.task_id)


def _decode_page_token(raw_value: str | None) -> _TaskCursor | None:
    if raw_value is None or not raw_value.strip():
        return None

    normalized = raw_value.strip()
    padding = "=" * (-len(normalized) % 4)
    try:
        decoded = base64.urlsafe_b64decode(normalized + padding).decode("utf-8")
        payload = json.loads(decoded)
        task_id = payload["id"]
        timestamp = payload["timestamp"]
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("id must be a non-empty string")
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise ValueError("timestamp must be a non-empty string")
        return _TaskCursor(
            task_id=task_id,
            timestamp=_parse_timestamp(timestamp, field="pageToken.timestamp"),
        )
    except Exception as exc:
        raise _ListTasksValidationError(
            field="pageToken",
            message="pageToken is invalid.",
        ) from exc


def _encode_page_token(task: Task) -> str:
    payload = json.dumps(
        {
            "id": task.id,
            "timestamp": _task_status_timestamp(task).isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def _invalid_argument_response(
    *,
    field: str,
    message: str,
    protocol_version: str,
) -> JSONResponse:
    return JSONResponse(
        build_http_error_body(
            protocol_version=protocol_version,
            status_code=400,
            status="INVALID_ARGUMENT",
            message=message,
            legacy_payload={"error": message, "field": field},
            reason="INVALID_LIST_TASKS_REQUEST",
            metadata={"field": field},
        ),
        status_code=400,
    )

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types import ListTasksRequest, Task, TaskState
from fastapi import Request
from fastapi.responses import JSONResponse
from google.protobuf.json_format import MessageToDict

from ..extension_negotiation import (
    filter_negotiated_extensions_from_payload,
)
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
from .task_store import TaskStoreOperationError

logger = logging.getLogger(__name__)
_DEFAULT_LIST_TASKS_PAGE_SIZE = 50
_MAX_LIST_TASKS_PAGE_SIZE = 100
_MIN_LIST_TASKS_PAGE_SIZE = 1
_LIST_TASKS_SCAN_BATCH_SIZE = 100


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


@dataclass(frozen=True)
class _ListTasksPage:
    tasks: list[Task]
    next_page_token: str
    total_size: int


class _ListTasksValidationError(ValueError):
    def __init__(self, *, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


def build_list_tasks_route(
    *,
    task_store: TaskStore,
    context_builder: Callable[[Request], ServerCallContext],
):
    async def list_tasks_route(request: Request) -> JSONResponse:
        try:
            query = _parse_list_tasks_query(request)
            page = await _list_tasks_page(
                task_store,
                query=query,
                context=context_builder(request),
            )
        except _ListTasksValidationError as error:
            return JSONResponse(
                build_http_error_body(
                    status_code=400,
                    status="INVALID_ARGUMENT",
                    message=error.message,
                    reason="INVALID_LIST_TASKS_REQUEST",
                    metadata={"field": error.field},
                ),
                status_code=400,
            )
        except TaskStoreOperationError as error:
            return JSONResponse(
                build_http_error_body(
                    status_code=500,
                    status="INTERNAL",
                    message="Task store unavailable while listing tasks.",
                    reason="TASK_STORE_UNAVAILABLE",
                    metadata={"operation": error.operation},
                ),
                status_code=500,
            )

        return JSONResponse(
            {
                "tasks": [
                    _serialize_task(
                        task,
                        history_length=query.history_length,
                        include_artifacts=query.include_artifacts,
                        requested_extensions=frozenset(
                            get_requested_extensions(request.headers.getlist(HTTP_EXTENSION_HEADER))
                        ),
                    )
                    for task in page.tasks
                ],
                "nextPageToken": page.next_page_token,
                "pageSize": len(page.tasks),
                "totalSize": page.total_size,
            }
        )

    return list_tasks_route


async def _list_tasks_page(
    task_store: TaskStore,
    *,
    query: _ListTasksQuery,
    context: ServerCallContext,
) -> _ListTasksPage:
    page_tasks: list[Task] = []
    total_size: int | None = None
    backend_page_token = ""
    has_more = False

    while True:
        response = await task_store.list(
            _build_list_tasks_request(
                query,
                page_size=max(query.requested_page_size, _LIST_TASKS_SCAN_BATCH_SIZE),
                page_token=backend_page_token,
            ),
            context,
        )
        if total_size is None:
            total_size = int(response.total_size)

        for task in response.tasks:
            if query.cursor is not None and _task_sort_key(task) >= _cursor_sort_key(query.cursor):
                continue
            page_tasks.append(task)
            if len(page_tasks) > query.requested_page_size:
                has_more = True
                break

        if has_more or not response.next_page_token:
            break
        backend_page_token = response.next_page_token

    tasks = page_tasks[: query.requested_page_size]
    next_page_token = _encode_page_token(tasks[-1]) if has_more and tasks else ""
    return _ListTasksPage(
        tasks=tasks,
        next_page_token=next_page_token,
        total_size=0 if total_size is None else total_size,
    )


def _build_list_tasks_request(
    query: _ListTasksQuery,
    *,
    page_size: int,
    page_token: str,
) -> ListTasksRequest:
    request = ListTasksRequest(
        context_id=query.context_id or "",
        include_artifacts=True,
        page_size=page_size,
        page_token=page_token,
    )
    if query.status is not None:
        request.status = query.status
    if query.status_timestamp_after is not None:
        request.status_timestamp_after = query.status_timestamp_after
    return request


def _serialize_task(
    task: Task,
    *,
    history_length: int,
    include_artifacts: bool,
    requested_extensions: frozenset[str],
) -> dict:
    negotiated = apply_accepted_output_modes(
        task,
        extract_accepted_output_modes_from_metadata(task.metadata),
    )
    if isinstance(negotiated, Task):
        task = negotiated
    task = filter_negotiated_extensions_from_payload(task, requested_extensions)

    payload = cast(dict[str, Any], MessageToDict(task))

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
            normalized_status = status_value.strip()
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
        error_factory=lambda error_field, _message: _ListTasksValidationError(
            field=error_field,
            message=f"{error_field} must be an integer.",
        ),
    )
    assert parsed is not None
    return parsed


def _parse_bool(raw_value: str | None, *, field: str, default: bool) -> bool:
    parsed = parse_shared_bool_field(
        raw_value,
        field=field,
        error_factory=lambda error_field, _message: _ListTasksValidationError(
            field=error_field,
            message=f"{error_field} must be a boolean.",
        ),
        true_values=("true", "1"),
        false_values=("false", "0"),
    )
    return default if parsed is None else parsed


def _parse_timestamp(raw_value: str, *, field: str) -> datetime:
    return parse_shared_timestamp_field(
        raw_value,
        field=field,
        error_factory=lambda error_field, message: _ListTasksValidationError(
            field=error_field,
            message=message,
        ),
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

from __future__ import annotations

import logging
from typing import Any

from a2a.types import A2AError, InternalError
from starlette.responses import Response

from ...contracts.extensions import SESSION_QUERY_ERROR_BUSINESS_CODES
from ...opencode_upstream_client import UpstreamConcurrencyLimitError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import (
    invalid_params_error,
    session_forbidden_error,
    upstream_http_error,
    upstream_payload_error,
    upstream_unreachable_error,
)

ERR_SESSION_FORBIDDEN = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_FORBIDDEN"]
logger = logging.getLogger(__name__)


def build_success_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    result: dict[str, Any],
) -> Response:
    if request_id is None:
        return Response(status_code=204)
    return context.success_response(request_id, result)


def build_session_forbidden_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    session_id: str,
) -> Response:
    return context.error_response(
        request_id,
        session_forbidden_error(ERR_SESSION_FORBIDDEN, session_id=session_id),
    )


def extract_directory_from_metadata(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    metadata = params.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return None, context.error_response(
            request_id,
            invalid_params_error(
                "metadata must be an object",
                data={"type": "INVALID_FIELD", "field": "metadata"},
            ),
        )

    opencode_metadata: dict[str, Any] | None = None
    if isinstance(metadata, dict):
        unknown_metadata_fields = sorted(set(metadata) - {"opencode", "shared"})
        if unknown_metadata_fields:
            prefixed_fields = [f"metadata.{field}" for field in unknown_metadata_fields]
            return None, context.error_response(
                request_id,
                invalid_params_error(
                    f"Unsupported metadata fields: {', '.join(prefixed_fields)}",
                    data={"type": "INVALID_FIELD", "fields": prefixed_fields},
                ),
            )
        raw_opencode_metadata = metadata.get("opencode")
        if raw_opencode_metadata is not None and not isinstance(raw_opencode_metadata, dict):
            return None, context.error_response(
                request_id,
                invalid_params_error(
                    "metadata.opencode must be an object",
                    data={"type": "INVALID_FIELD", "field": "metadata.opencode"},
                ),
            )
        if isinstance(raw_opencode_metadata, dict):
            opencode_metadata = raw_opencode_metadata
        raw_shared_metadata = metadata.get("shared")
        if raw_shared_metadata is not None and not isinstance(raw_shared_metadata, dict):
            return None, context.error_response(
                request_id,
                invalid_params_error(
                    "metadata.shared must be an object",
                    data={"type": "INVALID_FIELD", "field": "metadata.shared"},
                ),
            )

    directory = None
    if opencode_metadata is not None:
        directory = opencode_metadata.get("directory")
    if directory is not None and not isinstance(directory, str):
        return None, context.error_response(
            request_id,
            invalid_params_error(
                "metadata.opencode.directory must be a string",
                data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
            ),
        )

    return directory, None


def extract_workspace_id_from_metadata(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    metadata = params.get("metadata")
    if metadata is None:
        return None, None
    if not isinstance(metadata, dict):
        return None, context.error_response(
            request_id,
            invalid_params_error(
                "metadata must be an object",
                data={"type": "INVALID_FIELD", "field": "metadata"},
            ),
        )

    raw_opencode_metadata = metadata.get("opencode")
    if raw_opencode_metadata is None:
        return None, None
    if not isinstance(raw_opencode_metadata, dict):
        return None, context.error_response(
            request_id,
            invalid_params_error(
                "metadata.opencode must be an object",
                data={"type": "INVALID_FIELD", "field": "metadata.opencode"},
            ),
        )

    raw_workspace = raw_opencode_metadata.get("workspace")
    if raw_workspace is None:
        return None, None
    if not isinstance(raw_workspace, dict):
        return None, context.error_response(
            request_id,
            invalid_params_error(
                "metadata.opencode.workspace must be an object",
                data={"type": "INVALID_FIELD", "field": "metadata.opencode.workspace"},
            ),
        )

    raw_workspace_id = raw_workspace.get("id")
    if raw_workspace_id is None:
        return None, None
    if not isinstance(raw_workspace_id, str):
        return None, context.error_response(
            request_id,
            invalid_params_error(
                "metadata.opencode.workspace.id must be a string",
                data={"type": "INVALID_FIELD", "field": "metadata.opencode.workspace.id"},
            ),
        )
    workspace_id = raw_workspace_id.strip()
    return workspace_id or None, None


def resolve_routing_context(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
    requested_directory: str | None = None,
) -> tuple[str | None, str | None, Response | None]:
    workspace_id, workspace_error = extract_workspace_id_from_metadata(
        context,
        request_id=request_id,
        params=params,
    )
    if workspace_error is not None:
        return None, None, workspace_error
    if workspace_id is not None:
        return None, workspace_id, None

    if requested_directory is not None:
        try:
            return context.directory_resolver(requested_directory), None, None
        except ValueError as exc:
            return (
                None,
                None,
                context.error_response(
                    request_id,
                    invalid_params_error(
                        str(exc),
                        data={"type": "INVALID_FIELD", "field": "directory"},
                    ),
                ),
            )

    directory, directory_error = resolve_directory(
        context,
        request_id=request_id,
        params=params,
    )
    if directory_error is not None:
        return None, None, directory_error
    return directory, None, None


def resolve_directory(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    directory, metadata_error = extract_directory_from_metadata(
        context,
        request_id=request_id,
        params=params,
    )
    if metadata_error is not None:
        return None, metadata_error

    try:
        return context.directory_resolver(directory), None
    except ValueError as exc:
        return None, context.error_response(
            request_id,
            invalid_params_error(
                str(exc),
                data={"type": "INVALID_FIELD", "field": "metadata.opencode.directory"},
            ),
        )


def extract_interrupt_callback_directory_hint(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    # Historical contract: interrupt callbacks accept raw metadata.opencode.directory
    # and do not run it through the directory resolver used by session methods.
    return extract_directory_from_metadata(
        context,
        request_id=request_id,
        params=params,
    )


def build_upstream_http_error_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    code: int,
    *,
    upstream_status: int,
    method: str | None = None,
    session_id: str | None = None,
    interrupt_request_id: str | None = None,
    detail: str | None = None,
) -> Response:
    return context.error_response(
        request_id,
        upstream_http_error(
            code,
            upstream_status=upstream_status,
            method=method,
            session_id=session_id,
            request_id=interrupt_request_id,
            detail=detail,
        ),
    )


def build_upstream_unreachable_error_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    code: int,
    *,
    method: str | None = None,
    session_id: str | None = None,
    interrupt_request_id: str | None = None,
    detail: str | None = None,
) -> Response:
    return context.error_response(
        request_id,
        upstream_unreachable_error(
            code,
            method=method,
            session_id=session_id,
            request_id=interrupt_request_id,
            detail=detail,
        ),
    )


def build_upstream_concurrency_error_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    code: int,
    *,
    exc: UpstreamConcurrencyLimitError,
    method: str | None = None,
    session_id: str | None = None,
    interrupt_request_id: str | None = None,
) -> Response:
    return build_upstream_unreachable_error_response(
        context,
        request_id,
        code,
        method=method,
        session_id=session_id,
        interrupt_request_id=interrupt_request_id,
        detail=str(exc),
    )


def build_upstream_payload_error_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    code: int,
    *,
    detail: str,
    method: str | None = None,
    session_id: str | None = None,
    interrupt_request_id: str | None = None,
) -> Response:
    return context.error_response(
        request_id,
        upstream_payload_error(
            code,
            detail=detail,
            method=method,
            session_id=session_id,
            request_id=interrupt_request_id,
        ),
    )


def build_internal_error_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    log_message: str,
    exc: Exception,
) -> Response:
    logger.exception(log_message)
    return context.error_response(
        request_id,
        A2AError(root=InternalError(message=str(exc))),
    )

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from a2a.utils.errors import InternalError
from starlette.responses import Response

from ...contracts.extensions import SESSION_QUERY_ERROR_BUSINESS_CODES
from ...metadata_access import extract_namespaced_value
from ...opencode_upstream_client import UpstreamConcurrencyLimitError, UpstreamContractError
from ..dispatch import ExtensionHandlerContext
from ..error_responses import (
    authorization_forbidden_error,
    invalid_params_error,
    session_forbidden_error,
    upstream_http_error,
    upstream_payload_error,
    upstream_unreachable_error,
)

ERR_SESSION_FORBIDDEN = SESSION_QUERY_ERROR_BUSINESS_CODES["SESSION_FORBIDDEN"]
ERR_AUTHORIZATION_FORBIDDEN = SESSION_QUERY_ERROR_BUSINESS_CODES["AUTHORIZATION_FORBIDDEN"]
logger = logging.getLogger(__name__)


class SessionClaimGuard:
    def __init__(
        self,
        context: ExtensionHandlerContext,
        *,
        identity: str | None,
        session_id: str | None,
        logger: logging.Logger,
    ) -> None:
        self._context = context
        self._identity = identity
        self._session_id = session_id
        self._logger = logger
        self._pending = False
        self._finalized = False

    async def __aenter__(self) -> SessionClaimGuard:
        if self._identity and self._session_id:
            self._pending = await self._context.session_claim(
                identity=self._identity,
                session_id=self._session_id,
            )
        return self

    async def finalize(self) -> None:
        if self._pending and not self._finalized and self._identity and self._session_id:
            await self._context.session_claim_finalize(
                identity=self._identity,
                session_id=self._session_id,
            )
            self._finalized = True

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc_type, exc, tb
        if self._pending and not self._finalized and self._identity and self._session_id:
            try:
                await self._context.session_claim_release(
                    identity=self._identity,
                    session_id=self._session_id,
                )
            except Exception:
                self._logger.exception(
                    "Failed to release pending session claim for session_id=%s",
                    self._session_id,
                )
        return False
def build_success_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    result: dict[str, Any],
) -> Response:
    if request_id is None:
        return Response(status_code=204)
    return context.success_response(request_id, result)


def reject_unknown_fields(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    payload: dict[str, Any],
    *,
    allowed_fields: set[str] | frozenset[str],
    field_prefix: str = "",
    message_prefix: str = "Unsupported fields",
) -> Response | None:
    unknown_fields = sorted(set(payload) - set(allowed_fields))
    if not unknown_fields:
        return None
    reported_fields = (
        [f"{field_prefix}{field}" for field in unknown_fields] if field_prefix else unknown_fields
    )
    return context.error_response(
        request_id,
        invalid_params_error(
            f"{message_prefix}: {', '.join(reported_fields)}",
            data={"type": "INVALID_FIELD", "fields": reported_fields},
        ),
    )


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


def build_authorization_forbidden_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    method: str,
    capability: str,
    credential_id: str | None = None,
    error_code: int = ERR_AUTHORIZATION_FORBIDDEN,
) -> Response:
    return context.error_response(
        request_id,
        authorization_forbidden_error(
            error_code,
            method=method,
            capability=capability,
            credential_id=credential_id,
        ),
    )


def _parse_metadata_objects(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
    strict_top_level: bool = False,
    validate_shared_object: bool = False,
) -> tuple[dict[str, Any] | None, Response | None]:
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

    if strict_top_level:
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

    if validate_shared_object:
        raw_shared_metadata = metadata.get("shared")
        if raw_shared_metadata is not None and not isinstance(raw_shared_metadata, dict):
            return None, context.error_response(
                request_id,
                invalid_params_error(
                    "metadata.shared must be an object",
                    data={"type": "INVALID_FIELD", "field": "metadata.shared"},
                ),
            )

    return (
        raw_opencode_metadata if isinstance(raw_opencode_metadata, dict) else None,
        None,
    )


def extract_directory_from_metadata(
    context: ExtensionHandlerContext,
    *,
    request_id: str | int | None,
    params: dict[str, Any],
) -> tuple[str | None, Response | None]:
    opencode_metadata, metadata_error = _parse_metadata_objects(
        context,
        request_id=request_id,
        params=params,
        strict_top_level=True,
        validate_shared_object=True,
    )
    if metadata_error is not None:
        return None, metadata_error

    directory = None
    if opencode_metadata is not None:
        directory = extract_namespaced_value(
            {"opencode": opencode_metadata},
            namespace="opencode",
            path=("directory",),
        )
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
    raw_opencode_metadata, metadata_error = _parse_metadata_objects(
        context,
        request_id=request_id,
        params=params,
    )
    if metadata_error is not None:
        return None, metadata_error
    if raw_opencode_metadata is None:
        return None, None

    raw_workspace = extract_namespaced_value(
        {"opencode": raw_opencode_metadata},
        namespace="opencode",
        path=("workspace",),
    )
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

    raw_workspace_id = extract_namespaced_value(
        {"workspace": raw_workspace},
        namespace="workspace",
        path=("id",),
    )
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


async def invoke_upstream_or_error(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    invoke: Callable[[], Awaitable[Any]],
    upstream_http_error_code: int,
    upstream_unreachable_error_code: int,
    internal_log_message: str,
    method: str | None = None,
    session_id: str | None = None,
    interrupt_request_id: str | None = None,
    on_not_found: Callable[[], Response] | None = None,
) -> tuple[Any | None, Response | None]:
    try:
        return await invoke(), None
    except Exception as exc:
        return None, build_upstream_exception_response(
            context,
            request_id,
            exc=exc,
            upstream_http_error_code=upstream_http_error_code,
            upstream_unreachable_error_code=upstream_unreachable_error_code,
            internal_log_message=internal_log_message,
            method=method,
            session_id=session_id,
            interrupt_request_id=interrupt_request_id,
            on_not_found=on_not_found,
        )


def build_upstream_exception_response(
    context: ExtensionHandlerContext,
    request_id: str | int | None,
    *,
    exc: Exception,
    upstream_http_error_code: int,
    upstream_unreachable_error_code: int,
    internal_log_message: str,
    method: str | None = None,
    session_id: str | None = None,
    interrupt_request_id: str | None = None,
    upstream_payload_error_code: int | None = None,
    on_not_found: Callable[[], Response] | None = None,
    on_permission_error: Callable[[], Response] | None = None,
    payload_warning_message: str | None = None,
) -> Response:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 404 and on_not_found is not None:
            return on_not_found()
        return build_upstream_http_error_response(
            context,
            request_id,
            upstream_http_error_code,
            upstream_status=exc.response.status_code,
            method=method,
            session_id=session_id,
            interrupt_request_id=interrupt_request_id,
        )

    if isinstance(exc, httpx.HTTPError):
        return build_upstream_unreachable_error_response(
            context,
            request_id,
            upstream_unreachable_error_code,
            method=method,
            session_id=session_id,
            interrupt_request_id=interrupt_request_id,
        )

    if isinstance(exc, UpstreamConcurrencyLimitError):
        return build_upstream_concurrency_error_response(
            context,
            request_id,
            upstream_unreachable_error_code,
            exc=exc,
            method=method,
            session_id=session_id,
            interrupt_request_id=interrupt_request_id,
        )

    if upstream_payload_error_code is not None and isinstance(
        exc, (UpstreamContractError, ValueError)
    ):
        if isinstance(exc, ValueError) and payload_warning_message is not None:
            logger.warning("%s: %s", payload_warning_message, exc)
        return build_upstream_payload_error_response(
            context,
            request_id,
            upstream_payload_error_code,
            detail=str(exc),
            method=method,
            session_id=session_id,
            interrupt_request_id=interrupt_request_id,
        )

    if isinstance(exc, PermissionError) and on_permission_error is not None:
        return on_permission_error()

    return build_internal_error_response(
        context,
        request_id,
        log_message=internal_log_message,
        exc=exc,
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
        InternalError(message=str(exc)),
    )

from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar, Token
from typing import cast
from urllib.parse import urlsplit

from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from google.protobuf.json_format import MessageToDict
from starlette.responses import StreamingResponse

from ..a2a_protocol import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)
from ..auth import (
    authenticate_static_credential,
    build_static_auth_credentials,
)
from ..client.network_policy import matches_allowed_host
from ..contracts.extensions import ALL_EXTENSION_URIS, PUBLIC_EXTENSION_URIS
from ..execution.metrics import emit_metric
from ..jsonrpc.error_responses import (
    adapt_jsonrpc_error,
    build_http_error_body,
    version_not_supported_error,
)
from ..jsonrpc.models import JSONRPCError
from ..protocol_versions import (
    UnsupportedProtocolVersionError,
    negotiate_protocol_version,
)
from ..trace_context import (
    TRACEPARENT_HEADER,
    TRACESTATE_HEADER,
    reset_current_trace_context,
    resolve_trace_context,
    set_current_trace_context,
)
from .request_parsing import (
    _decode_payload_preview,
    _detect_sensitive_extension_method,
    _is_json_content_type,
    _looks_like_jsonrpc_envelope,
    _normalize_content_type,
    _parse_content_length,
    _parse_json_body,
    _request_body_too_large_response,
    _RequestBodyTooLargeError,
)

logger = logging.getLogger("opencode_a2a.server.application")
PUBLIC_AGENT_CARD_CACHE_CONTROL = "public, max-age=300"
AUTHENTICATED_EXTENDED_CARD_CACHE_CONTROL = "private, max-age=300"
_REQUEST_BODY_BYTES: ContextVar[bytes | None] = ContextVar(
    "_REQUEST_BODY_BYTES",
    default=None,
)


def _origin_of_url(value: str) -> str | None:
    """Return the normalized ``scheme://host[:port]`` origin of a URL."""

    parsed = urlsplit((value or "").strip())
    scheme = (parsed.scheme or "").lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    hostname = hostname.lower()
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{hostname}"
    return f"{scheme}://{hostname}:{port}"


def _normalized_origins(values) -> set[str]:  # noqa: ANN001
    normalized: set[str] = set()
    for raw in values or ():
        value = (raw or "").strip().lower().rstrip("/")
        if value:
            normalized.add(value)
    return normalized


_LOOPBACK_BIND_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_bind(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if normalized in _LOOPBACK_BIND_HOSTS:
        return True
    return normalized.startswith("127.")


def _hostname_from_host_header(host: str) -> str:
    try:
        parsed = urlsplit(f"//{host.strip()}")
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def _boundary_rejection_response(message: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": 403,
                "status": "FORBIDDEN",
                "message": message,
            }
        },
        status_code=403,
    )


def _is_http_json_rest_path(path: str) -> bool:
    """Return True for root-based A2A HTTP+JSON REST paths.

    A2A 1.0 resolves REST paths relative to the advertised interface URL
    (spec sections 8.5/8.6), so this service serves the HTTP+JSON surface at
    the root: no version prefix in the URL path, with protocol versioning
    carried in the A2A-Version header instead.
    """
    return path.startswith("/message:") or path.startswith("/tasks")


def add_auth_middleware(app: FastAPI, settings) -> None:  # noqa: ANN001
    configured_credentials = build_static_auth_credentials(settings)
    advertised_schemes = {credential.auth_scheme for credential in configured_credentials}

    def _unauthorized_response() -> JSONResponse:
        challenges: list[str] = []
        if "bearer" in advertised_schemes:
            challenges.append("Bearer")
        if "basic" in advertised_schemes:
            challenges.append('Basic realm="opencode-a2a"')
        return JSONResponse(
            {"error": "Unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": ", ".join(challenges)},
        )

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in {
            AGENT_CARD_WELL_KNOWN_PATH,
        }:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        try:
            auth_scheme, auth_value = auth_header.split(" ", 1)
        except ValueError:
            return _unauthorized_response()
        provided = auth_value.strip()

        principal = authenticate_static_credential(
            credentials=configured_credentials,
            auth_scheme=auth_scheme,
            auth_value=provided,
        )
        if principal is None:
            return _unauthorized_response()
        request.state.authenticated_principal = principal
        request.state.user_identity = principal.identity
        request.state.user_auth_scheme = principal.auth_scheme
        if principal.credential_id:
            request.state.user_credential_id = principal.credential_id

        return await call_next(request)


def add_http_boundary_middleware(app: FastAPI, settings) -> None:  # noqa: ANN001
    """Enforce the inbound Origin/Host boundary (CSRF and DNS rebinding guard).

    Browsers attach stored Basic credentials to every request and send an
    ``Origin`` header, so a cross-origin page could otherwise trigger task
    submission, cancellation, or subscription. Requests carrying an ``Origin``
    header must match the origin derived from ``A2A_PUBLIC_URL`` or an entry in
    ``A2A_ALLOWED_ORIGINS``; requests without an ``Origin`` header (CLI/SDK
    clients) are unaffected.

    When ``A2A_ALLOWED_HOSTS`` is configured, the ``Host`` header is validated
    for every request (exact names or ``*.example.com`` wildcards). Binding to
    a non-loopback address without a host allowlist logs a startup warning
    because the service is then exposed to DNS rebinding.
    """

    allowed_origins = _normalized_origins(getattr(settings, "a2a_allowed_origins", ()))
    public_origin = _origin_of_url(getattr(settings, "a2a_public_url", ""))
    if public_origin is not None:
        allowed_origins.add(public_origin)
    else:
        logger.warning(
            "A2A_PUBLIC_URL=%r is not a valid http(s) URL; requests carrying an "
            "Origin header will be rejected unless A2A_ALLOWED_ORIGINS matches",
            getattr(settings, "a2a_public_url", ""),
        )
    allowed_hosts = tuple(getattr(settings, "a2a_allowed_hosts", ()) or ())
    allowed_host_headers = {entry.strip().lower() for entry in allowed_hosts if entry.strip()}
    enforce_host = bool(allowed_hosts)
    if not enforce_host and not _is_loopback_bind(getattr(settings, "a2a_host", "127.0.0.1")):
        logger.warning(
            "A2A server is bound to non-loopback host=%s without A2A_ALLOWED_HOSTS; "
            "set a Host allowlist to defend against DNS rebinding",
            getattr(settings, "a2a_host", "127.0.0.1"),
        )

    @app.middleware("http")
    async def enforce_http_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None:
            normalized_origin = origin.strip().lower().rstrip("/")
            if normalized_origin not in allowed_origins:
                return _boundary_rejection_response("Cross-origin request rejected")

        if enforce_host:
            host = request.headers.get("host")
            hostname = _hostname_from_host_header(host or "")
            host_header_allowed = (host or "").strip().lower() in allowed_host_headers
            if not hostname or not (
                host_header_allowed or matches_allowed_host(hostname, allowed_hosts)
            ):
                return _boundary_rejection_response("Host not allowed")

        return await call_next(request)


def build_agent_card_etag(card) -> str:  # noqa: ANN001
    payload = MessageToDict(card)
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f'W/"{hashlib.sha256(content).hexdigest()}"'


def install_runtime_middlewares(
    app: FastAPI,
    settings,
    *,
    public_card_etag: str,
    extended_card_etag: str,
) -> None:
    def _requires_protocol_negotiation(request: Request) -> bool:
        if request.url.path == "/" and request.method == "POST":
            return True
        if _is_http_json_rest_path(request.url.path):
            return True
        return False

    def _extract_jsonrpc_request_id(payload: object) -> str | int | None:
        if not isinstance(payload, dict):
            return None
        request_id = payload.get("id")
        if isinstance(request_id, str | int):
            return request_id
        return None

    @app.middleware("http")
    async def bind_trace_context(request: Request, call_next):
        trace_context = resolve_trace_context(
            request.headers.get(TRACEPARENT_HEADER),
            request.headers.get(TRACESTATE_HEADER),
        )
        request.state.traceparent = trace_context.traceparent
        request.state.trace_id = trace_context.trace_id
        if trace_context.tracestate:
            request.state.tracestate = trace_context.tracestate
        token = set_current_trace_context(trace_context)
        try:
            response = await call_next(request)
        finally:
            reset_current_trace_context(token)
        response.headers[TRACEPARENT_HEADER] = trace_context.traceparent
        return response

    @app.middleware("http")
    async def negotiate_a2a_protocol_version(request: Request, call_next):
        token: Token | None = None
        if not _requires_protocol_negotiation(request):
            return await call_next(request)

        try:
            negotiated_version = negotiate_protocol_version(
                header_value=request.headers.get("A2A-Version"),
                query_value=request.query_params.get("A2A-Version"),
            )
        except UnsupportedProtocolVersionError as error:
            if request.url.path == "/" and request.method == "POST":
                try:
                    body, token = await _get_request_body(request)
                    payload = _parse_json_body(body)
                except _RequestBodyTooLargeError as request_error:
                    return _request_body_too_large_response(
                        path=request.url.path,
                        method=request.method,
                        error=request_error,
                    )
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": _extract_jsonrpc_request_id(payload),
                        "error": cast(
                            JSONRPCError,
                            adapt_jsonrpc_error(
                                version_not_supported_error(
                                    requested_version=error.requested_version,
                                    supported_protocol_versions=list(
                                        error.supported_protocol_versions
                                    ),
                                    default_protocol_version=error.default_protocol_version,
                                ),
                            ),
                        ).model_dump(mode="json", exclude_none=True),
                    },
                    status_code=200,
                )
            return JSONResponse(
                build_http_error_body(
                    status_code=400,
                    status="INVALID_ARGUMENT",
                    message="Unsupported A2A version",
                    reason="VERSION_NOT_SUPPORTED",
                    metadata={
                        "requested_version": error.requested_version,
                        "supported_protocol_versions": list(error.supported_protocol_versions),
                        "default_protocol_version": error.default_protocol_version,
                    },
                ),
                status_code=400,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

        request.state.a2a_protocol_version = negotiated_version
        response = await call_next(request)
        response.headers["A2A-Version"] = negotiated_version
        _set_activated_extensions_header(response, request)
        return response

    async def _get_request_body(request: Request) -> tuple[bytes, Token | None]:
        cached = _REQUEST_BODY_BYTES.get()
        if cached is not None:
            return cached, None

        limit = settings.a2a_max_request_body_bytes
        content_length = _parse_content_length(request.headers.get("content-length"))
        if limit > 0 and content_length is not None and content_length > limit:
            raise _RequestBodyTooLargeError(limit=limit, actual_size=content_length)

        if hasattr(request, "_body"):
            body = request._body
            if limit > 0 and len(body) > limit:
                raise _RequestBodyTooLargeError(limit=limit, actual_size=len(body))
        elif limit <= 0:
            body = await request.body()
        else:
            total = 0
            chunks: list[bytes] = []
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise _RequestBodyTooLargeError(limit=limit, actual_size=total)
                chunks.append(chunk)
            body = b"".join(chunks)
            request._body = body

        token = _REQUEST_BODY_BYTES.set(body)
        return body, token

    def _etag_matches(if_none_match: str | None, etag: str) -> bool:
        if not if_none_match:
            return False
        candidates = {item.strip() for item in if_none_match.split(",") if item.strip()}
        return "*" in candidates or etag in candidates

    def _merge_vary(*values: str) -> str:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            for item in value.split(","):
                normalized = item.strip()
                if not normalized:
                    continue
                key = normalized.lower()
                if key in seen:
                    continue
                seen.add(key)
                ordered.append(normalized)
        return ", ".join(ordered)

    def _resolve_activated_extensions(request: Request) -> tuple[str, ...]:
        explicit = getattr(request.state, "activated_extensions", None)
        if explicit is not None:
            return tuple(value for value in explicit if value)

        requested_extensions = tuple(
            get_requested_extensions(request.headers.getlist(HTTP_EXTENSION_HEADER))
        )
        if not requested_extensions:
            return ()

        path = request.url.path
        if path == AGENT_CARD_WELL_KNOWN_PATH:
            supported_extensions = PUBLIC_EXTENSION_URIS
        elif path == EXTENDED_AGENT_CARD_PATH:
            supported_extensions = ALL_EXTENSION_URIS
        elif path == "/" or _is_http_json_rest_path(path):
            supported_extensions = PUBLIC_EXTENSION_URIS
        else:
            return ()

        requested = frozenset(requested_extensions)
        return tuple(value for value in supported_extensions if value in requested)

    def _set_activated_extensions_header(response: Response, request: Request) -> None:
        if not (200 <= response.status_code < 400):
            return
        activated_extensions = _resolve_activated_extensions(request)
        if activated_extensions:
            response.headers[HTTP_EXTENSION_HEADER] = ",".join(activated_extensions)

    @app.middleware("http")
    async def cache_agent_card_responses(request: Request, call_next):
        if request.method != "GET":
            return await call_next(request)

        path = request.url.path
        is_public_card = path == AGENT_CARD_WELL_KNOWN_PATH
        is_extended_card = path == EXTENDED_AGENT_CARD_PATH
        if not is_public_card and not is_extended_card:
            return await call_next(request)

        if is_public_card and _etag_matches(request.headers.get("if-none-match"), public_card_etag):
            response = Response(
                status_code=304,
                headers={
                    "ETag": public_card_etag,
                    "Cache-Control": PUBLIC_AGENT_CARD_CACHE_CONTROL,
                    "Vary": "Accept-Encoding",
                },
            )
            _set_activated_extensions_header(response, request)
            return response

        response = await call_next(request)
        if response.status_code != 200:
            return response

        if is_public_card:
            response.headers["ETag"] = public_card_etag
            response.headers["Cache-Control"] = PUBLIC_AGENT_CARD_CACHE_CONTROL
            response.headers["Vary"] = _merge_vary(
                response.headers.get("Vary", ""),
                "Accept-Encoding",
            )
            _set_activated_extensions_header(response, request)
            return response

        response.headers["ETag"] = extended_card_etag
        response.headers["Cache-Control"] = AUTHENTICATED_EXTENDED_CARD_CACHE_CONTROL
        response.headers["Vary"] = _merge_vary(
            response.headers.get("Vary", ""),
            "Authorization",
            "Accept-Encoding",
        )
        if _etag_matches(request.headers.get("if-none-match"), extended_card_etag):
            cached_response = Response(status_code=304, headers=dict(response.headers))
            _set_activated_extensions_header(cached_response, request)
            return cached_response
        _set_activated_extensions_header(response, request)
        return response

    @app.middleware("http")
    async def enforce_request_body_limit(request: Request, call_next):
        token: Token | None = None
        limit = settings.a2a_max_request_body_bytes
        if limit <= 0 or request.method not in {"POST", "PUT", "PATCH"}:
            return await call_next(request)

        try:
            _, token = await _get_request_body(request)
            return await call_next(request)
        except _RequestBodyTooLargeError as error:
            return _request_body_too_large_response(
                path=request.url.path,
                method=request.method,
                error=error,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    @app.middleware("http")
    async def guard_rest_payload_shape(request: Request, call_next):
        token: Token | None = None
        if request.method != "POST" or request.url.path not in {
            "/message:send",
            "/message:stream",
        }:
            return await call_next(request)

        try:
            body, token = await _get_request_body(request)
            payload = _parse_json_body(body)
            if _looks_like_jsonrpc_envelope(payload):
                return JSONResponse(
                    build_http_error_body(
                        status_code=400,
                        status="INVALID_ARGUMENT",
                        message=(
                            "Invalid JSON-RPC payload for REST endpoint. "
                            "Call POST / for JSON-RPC methods such as SendMessage "
                            "or SendStreamingMessage, or send ProtoJSON "
                            "SendMessageRequest payloads to the REST endpoint."
                        ),
                        reason="INVALID_HTTP_JSON_PAYLOAD",
                        metadata={"path": request.url.path},
                    ),
                    status_code=400,
                )
            return await call_next(request)
        except _RequestBodyTooLargeError as error:
            return _request_body_too_large_response(
                path=request.url.path,
                method=request.method,
                error=error,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    @app.middleware("http")
    async def log_payloads(request: Request, call_next):
        token: Token | None = None
        if not settings.a2a_log_payloads:
            return await call_next(request)

        try:
            path = request.url.path
            limit = settings.a2a_log_body_limit
            content_type = _normalize_content_type(request.headers.get("content-type"))
            content_length = _parse_content_length(request.headers.get("content-length"))

            sensitive_method: str | None = None
            request_omit_reason: str | None = None

            if not _is_json_content_type(content_type):
                request_omit_reason = f"non-json content-type={content_type or 'unknown'}"
            elif limit > 0 and content_length is None:
                request_omit_reason = f"missing content-length with limit={limit}"
            elif limit > 0 and content_length is not None and content_length > limit:
                request_omit_reason = f"content-length={content_length} exceeds limit={limit}"
            else:
                body, token = await _get_request_body(request)
                payload = _parse_json_body(body)
                sensitive_method = _detect_sensitive_extension_method(payload)

                if sensitive_method:
                    logger.debug(
                        "A2A request %s %s method=%s",
                        request.method,
                        path,
                        sensitive_method,
                    )
                else:
                    logger.debug(
                        "A2A request %s %s body=%s",
                        request.method,
                        path,
                        _decode_payload_preview(body, limit=limit),
                    )

            if request_omit_reason:
                logger.debug(
                    "A2A request %s %s body=[omitted %s]",
                    request.method,
                    path,
                    request_omit_reason,
                )

            response = await call_next(request)
            if isinstance(response, StreamingResponse):
                if sensitive_method:
                    logger.debug("A2A response %s streaming method=%s", path, sensitive_method)
                else:
                    logger.debug("A2A response %s streaming", path)
                return response

            response_body = getattr(response, "body", b"") or b""
            if sensitive_method:
                logger.debug(
                    "A2A response %s status=%s bytes=%s method=%s",
                    path,
                    response.status_code,
                    len(response_body),
                    sensitive_method,
                )
                return response

            if request_omit_reason:
                logger.debug(
                    "A2A response %s status=%s bytes=%s body=[omitted request_%s]",
                    path,
                    response.status_code,
                    len(response_body),
                    request_omit_reason,
                )
                return response
            response_content_type = _normalize_content_type(response.headers.get("content-type"))
            if not _is_json_content_type(response_content_type):
                logger.debug(
                    "A2A response %s status=%s bytes=%s body=[omitted non-json content-type=%s]",
                    path,
                    response.status_code,
                    len(response_body),
                    response_content_type or "unknown",
                )
                return response

            logger.debug(
                "A2A response %s status=%s body=%s",
                path,
                response.status_code,
                _decode_payload_preview(response_body, limit=limit),
            )
            return response
        except _RequestBodyTooLargeError as error:
            return _request_body_too_large_response(
                path=request.url.path,
                method=request.method,
                error=error,
            )
        finally:
            if token is not None:
                _REQUEST_BODY_BYTES.reset(token)

    add_auth_middleware(app, settings)
    add_http_boundary_middleware(app, settings)


def emit_stream_request_metrics(*, active_delta: float | None = None) -> None:
    if active_delta is None:
        emit_metric("a2a_stream_requests_total")
        return
    emit_metric("a2a_stream_active", active_delta)

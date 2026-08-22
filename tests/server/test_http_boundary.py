from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from opencode_a2a.config import Settings
from opencode_a2a.server.middleware import add_http_boundary_middleware
from tests.support.settings import make_settings


def _boundary_app(settings: Settings) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True})

    add_http_boundary_middleware(app, settings)
    return app


async def _request(
    app: FastAPI, *, origin: str | None = None, host: str | None = None
) -> httpx.Response:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health", headers=headers)


@pytest.mark.asyncio
async def test_same_origin_request_is_allowed() -> None:
    settings = make_settings(a2a_public_url="http://127.0.0.1:8000")
    response = await _request(_boundary_app(settings), origin="http://127.0.0.1:8000")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cross_origin_request_is_rejected() -> None:
    settings = make_settings(a2a_public_url="http://127.0.0.1:8000")
    response = await _request(_boundary_app(settings), origin="https://evil.example")

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Cross-origin request rejected"


@pytest.mark.asyncio
async def test_request_without_origin_header_passes() -> None:
    settings = make_settings(a2a_public_url="http://127.0.0.1:8000")
    response = await _request(_boundary_app(settings), origin=None)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_configured_allowed_origin_passes() -> None:
    settings = make_settings(
        a2a_public_url="http://127.0.0.1:8000",
        a2a_allowed_origins=("https://dashboard.example",),
    )
    response = await _request(_boundary_app(settings), origin="https://dashboard.example")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_null_origin_is_rejected_by_default() -> None:
    settings = make_settings(a2a_public_url="http://127.0.0.1:8000")
    response = await _request(_boundary_app(settings), origin="null")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_invalid_public_url_rejects_origin_requests() -> None:
    settings = make_settings(a2a_public_url="localhost:8000")
    response = await _request(_boundary_app(settings), origin="http://127.0.0.1:8000")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_invalid_public_url_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    settings = make_settings(a2a_public_url="localhost:8000")
    with caplog.at_level(logging.WARNING, logger="opencode_a2a.server.application"):
        _boundary_app(settings)

    assert any("not a valid http(s) URL" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_options_preflight_with_cross_origin_is_rejected() -> None:
    settings = make_settings(a2a_public_url="http://127.0.0.1:8000")
    app = _boundary_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/message:send",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_host_allowlist_accepts_matching_host() -> None:
    settings = make_settings(a2a_allowed_hosts=("a2a.example.com",))
    response = await _request(
        _boundary_app(settings),
        origin="http://127.0.0.1:8000",
        host="a2a.example.com",
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_host_allowlist_rejects_mismatched_host() -> None:
    settings = make_settings(a2a_allowed_hosts=("a2a.example.com",))
    response = await _request(
        _boundary_app(settings),
        origin="http://127.0.0.1:8000",
        host="evil.example",
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == "Host not allowed"


@pytest.mark.asyncio
async def test_host_allowlist_wildcard_matches_subdomain() -> None:
    settings = make_settings(a2a_allowed_hosts=("*.example.com",))
    response = await _request(
        _boundary_app(settings),
        origin="http://127.0.0.1:8000",
        host="tenant-a.example.com:8000",
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_host_allowlist_rejects_apex_for_wildcard_entry() -> None:
    settings = make_settings(a2a_allowed_hosts=("*.example.com",))
    response = await _request(
        _boundary_app(settings),
        origin="http://127.0.0.1:8000",
        host="example.com",
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_host_allowlist_accepts_entry_with_port() -> None:
    settings = make_settings(a2a_allowed_hosts=("a2a.example.com:8000",))
    response = await _request(
        _boundary_app(settings),
        origin="http://127.0.0.1:8000",
        host="a2a.example.com:8000",
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_missing_host_is_rejected_when_allowlist_configured() -> None:
    settings = make_settings(a2a_allowed_hosts=("a2a.example.com",))
    response = await _request(_boundary_app(settings), origin=None, host=None)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_non_loopback_bind_without_allowlist_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(a2a_host="0.0.0.0")
    with caplog.at_level(logging.WARNING, logger="opencode_a2a.server.application"):
        _boundary_app(settings)

    assert any(
        "non-loopback" in record.message and "A2A_ALLOWED_HOSTS" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_loopback_bind_without_allowlist_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(a2a_host="127.0.0.1")
    with caplog.at_level(logging.WARNING, logger="opencode_a2a.server.application"):
        _boundary_app(settings)

    assert not any(
        "non-loopback" in record.message and "A2A_ALLOWED_HOSTS" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_public_url_host_not_covered_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(
        a2a_allowed_hosts=("a2a.example.com",),
        a2a_public_url="http://other.example:8000",
    )
    with caplog.at_level(logging.WARNING, logger="opencode_a2a.server.application"):
        _boundary_app(settings)

    assert any("not covered by A2A_ALLOWED_HOSTS" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_public_url_host_covered_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = make_settings(
        a2a_allowed_hosts=("a2a.example.com",),
        a2a_public_url="http://a2a.example.com:8000",
    )
    with caplog.at_level(logging.WARNING, logger="opencode_a2a.server.application"):
        _boundary_app(settings)

    assert not any(
        "not covered by A2A_ALLOWED_HOSTS" in record.message for record in caplog.records
    )

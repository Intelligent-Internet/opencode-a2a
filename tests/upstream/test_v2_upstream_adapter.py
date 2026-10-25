from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from opencode_a2a.opencode_upstream_client import UpstreamContractError
from opencode_a2a.upstream.v2 import UnsupportedUpstreamOperationError, V2UpstreamAdapter
from tests.support.settings import make_settings


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    *,
    workspace_root: str,
) -> tuple[V2UpstreamAdapter, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    async def capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return await handler(request)

    client = V2UpstreamAdapter(
        make_settings(
            opencode_workspace_root=workspace_root,
            opencode_auth_username="gateway",
            opencode_auth_password="test-password",  # pragma: allowlist secret
        )
    )
    monkeypatch.setattr(client._client, "_transport", httpx.MockTransport(capture))
    return client, requests


@pytest.mark.asyncio
async def test_v2_session_calls_use_documented_routes_locations_and_data_envelopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/info":
            payload: Any = {"version": "2.0.28", "pid": 123, "urls": []}
        elif request.url.path == "/api/location":
            payload = {"directory": str(workspace)}
        elif request.method == "POST":
            payload = {"data": {"id": "ses-created", "title": "topic"}}
        elif request.url.path == "/api/session":
            payload = {"data": [{"id": "ses-created"}], "cursor": {"next": "cursor-2"}}
        else:
            payload = {"data": {"id": "ses-created", "title": "topic"}}
        return httpx.Response(200, json=payload)

    client, requests = _make_client(monkeypatch, respond, workspace_root=str(workspace))
    try:
        identity = await client.get_server_info()
        location = await client.get_location()
        session_id = await client.create_session("topic")
        session = await client.get_session(session_id)
        page = await client.list_sessions(directory=str(workspace), params={"limit": 5})
    finally:
        await client.close()

    assert identity["version"] == "2.0.28"
    assert location["directory"] == str(workspace)
    assert session_id == "ses-created"
    assert session["title"] == "topic"
    assert page == {"data": [{"id": "ses-created"}], "cursor": {"next": "cursor-2"}}
    assert [request.url.path for request in requests] == [
        "/api/info",
        "/api/location",
        "/api/session",
        "/api/session/ses-created",
        "/api/session",
    ]
    assert requests[1].url.params["location[directory]"] == str(workspace)
    assert json.loads(requests[2].content) == {
        "location": {"directory": str(workspace)},
        "title": "topic",
    }
    assert requests[3].url.params["location[directory]"] == str(workspace)
    assert requests[4].url.params["location[directory]"] == str(workspace)
    assert requests[4].url.params["limit"] == "5"
    assert requests[0].headers["authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_v2_rejects_outside_workspace_and_v1_workspace_id_before_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def respond(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unauthorized request reached the upstream")

    client, requests = _make_client(monkeypatch, respond, workspace_root=str(workspace))
    escaped = tmp_path / "outside"
    escaped.mkdir()
    (workspace / "escape").symlink_to(escaped, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            await client.create_session("topic", directory=str(tmp_path))
        with pytest.raises(ValueError, match="outside the allowed workspace"):
            await client.create_session("topic", directory=str(workspace / "escape"))
        with pytest.raises(UnsupportedUpstreamOperationError, match="workspace IDs"):
            await client.create_session("topic", workspace_id="workspace-1")
        with pytest.raises(ValueError, match="location is selected"):
            await client.list_sessions(params={"location[directory]": str(tmp_path)})
    finally:
        await client.close()

    assert requests == []


@pytest.mark.asyncio
async def test_v2_requires_data_envelope_and_reports_mismatched_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def missing_envelope(_request: httpx.Request) -> httpx.Response:
        if _request.url.path == "/api/info":
            return httpx.Response(200, json={"version": "2.0.28"})
        return httpx.Response(200, json={"id": "ses-unwrapped"})

    client, _ = _make_client(monkeypatch, missing_envelope, workspace_root=str(workspace))
    try:
        with pytest.raises(UpstreamContractError, match="data envelope"):
            await client.create_session("topic")
    finally:
        await client.close()

    async def html_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html>wrong upstream</html>", headers={"content-type": "text/html"}
        )

    client, _ = _make_client(monkeypatch, html_response, workspace_root=str(workspace))
    try:
        with pytest.raises(UpstreamContractError, match="returned non-JSON response"):
            await client.get_server_info()
    finally:
        await client.close()

    async def not_found(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client, _ = _make_client(monkeypatch, not_found, workspace_root=str(workspace))
    try:
        with pytest.raises(UpstreamContractError, match="verify the upstream is an OpenCode V2"):
            await client.get_server_info()
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", (401, 409))
async def test_v2_session_http_status_errors_are_typed_without_leaking_request_data(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request_title = "private-session-request-title"
    observed_requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/info":
            return httpx.Response(200, json={"version": "2.0.28"})
        observed_requests.append(request)
        echoed_request = request.content.decode()
        echoed_authorization = request.headers.get("authorization", "")
        return httpx.Response(
            status_code,
            json={
                "message": "upstream rejected request",
                "request": echoed_request,
                "authorization": echoed_authorization,
            },
        )

    client, _ = _make_client(monkeypatch, respond, workspace_root=str(workspace))
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await client.create_session(request_title)
    finally:
        await client.close()

    error = exc_info.value
    message = str(error)
    assert error.response.status_code == status_code
    assert error.request.url.path == "/api/session"
    assert len(observed_requests) == 1
    assert request_title.encode() in observed_requests[0].content
    assert "Basic " in observed_requests[0].headers["authorization"]
    assert request_title not in message
    assert observed_requests[0].headers["authorization"] not in message
    assert "test-password" not in message


@pytest.mark.asyncio
async def test_v2_prompt_methods_fail_without_http_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def respond(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("prompt endpoint must not be reached")

    client, requests = _make_client(monkeypatch, respond, workspace_root=str(workspace))
    try:
        with pytest.raises(
            UnsupportedUpstreamOperationError,
            match="prompt execution is not implemented",
        ):
            await client.send_message("ses-1", "hello")
        with pytest.raises(
            UnsupportedUpstreamOperationError,
            match="prompt execution is not implemented",
        ):
            await client.submit_prompt("ses-1", "hello")
    finally:
        await client.close()

    assert requests == []


@pytest.mark.asyncio
async def test_v2_refuses_to_use_session_route_when_info_reports_v1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def v1_server(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/info"
        return httpx.Response(200, json={"version": "1.18.19"})

    client, requests = _make_client(monkeypatch, v1_server, workspace_root=str(workspace))
    try:
        with pytest.raises(UpstreamContractError, match="V2 was selected but /api/info reports"):
            await client.create_session("topic")
    finally:
        await client.close()

    assert [request.url.path for request in requests] == ["/api/info"]

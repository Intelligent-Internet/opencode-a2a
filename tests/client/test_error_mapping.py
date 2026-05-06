from __future__ import annotations

import httpx
import pytest
from a2a.client.errors import (
    A2AClientError as SDKClientError,
)
from a2a.client.errors import (
    A2AClientTimeoutError,
    AgentCardResolutionError,
)
from a2a.utils.errors import (
    A2AError,
    InvalidParamsError,
    MethodNotFoundError,
    TaskNotFoundError,
    VersionNotSupportedError,
)

from opencode_a2a.client.error_mapping import (
    map_a2a_error,
    map_agent_card_error,
    map_client_error,
    map_http_error,
    map_jsonrpc_error,
    map_operation_error,
)
from opencode_a2a.client.errors import (
    A2AAgentUnavailableError,
    A2AAuthenticationError,
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2APermissionDeniedError,
    A2ATimeoutError,
    A2AUnsupportedOperationError,
)
from opencode_a2a.jsonrpc.models import JSONRPCError, JSONRPCErrorResponse
from tests.support.fake_client_errors import (
    FakeA2AClientHTTPError,
    FakeA2AClientJSONError,
    FakeA2AClientJSONRPCError,
)


@pytest.mark.parametrize(
    ("exc", "expected_type", "error_code", "data"),
    [
        pytest.param(
            TaskNotFoundError("missing"),
            A2AUnsupportedOperationError,
            "task_not_found",
            None,
            id="task-not-found",
        ),
        pytest.param(
            MethodNotFoundError("unsupported"),
            A2AUnsupportedOperationError,
            "method_not_supported",
            None,
            id="method-not-found",
        ),
        pytest.param(
            InvalidParamsError("bad", data={"field": "limit"}),
            A2APeerProtocolError,
            "invalid_params",
            {"field": "limit"},
            id="invalid-params",
        ),
        pytest.param(
            VersionNotSupportedError("bad version", data={"version": "2.0"}),
            A2AUnsupportedOperationError,
            "version_not_supported",
            {"version": "2.0"},
            id="unsupported-version",
        ),
        pytest.param(
            A2AError("generic", data={"detail": "boom"}),
            A2APeerProtocolError,
            "peer_protocol_error",
            {"detail": "boom"},
            id="generic-a2a-error",
        ),
    ],
)
def test_map_a2a_error_variants(
    exc: A2AError,
    expected_type: type[Exception],
    error_code: str,
    data: object | None,
) -> None:
    mapped = map_a2a_error(exc)

    assert isinstance(mapped, expected_type)
    assert mapped.error_code == error_code
    assert mapped.data == data


@pytest.mark.parametrize(
    ("exc", "expected_type", "http_status"),
    [
        pytest.param(
            FakeA2AClientHTTPError(401, "denied"),
            A2AAuthenticationError,
            401,
            id="401",
        ),
        pytest.param(
            FakeA2AClientHTTPError(403, "forbidden"),
            A2APermissionDeniedError,
            403,
            id="403",
        ),
        pytest.param(
            FakeA2AClientHTTPError(404, "missing"),
            A2AUnsupportedOperationError,
            404,
            id="404",
        ),
        pytest.param(
            FakeA2AClientHTTPError(408, "slow"),
            A2ATimeoutError,
            408,
            id="408",
        ),
        pytest.param(
            SDKClientError("HTTP Error 503: busy"),
            A2AClientResetRequiredError,
            503,
            id="503-from-message",
        ),
        pytest.param(
            FakeA2AClientHTTPError(500, "boom"),
            A2AAgentUnavailableError,
            500,
            id="500",
        ),
    ],
)
def test_map_client_error_http_variants(
    exc: SDKClientError,
    expected_type: type[Exception],
    http_status: int,
) -> None:
    mapped = map_client_error("SendMessage", exc)

    assert isinstance(mapped, expected_type)
    assert mapped.http_status == http_status


def test_map_client_error_timeout_variant() -> None:
    mapped = map_client_error("SendMessage", A2AClientTimeoutError("timed out"))

    assert isinstance(mapped, A2ATimeoutError)
    assert mapped.http_status is None


def test_map_client_error_without_status_returns_protocol_error() -> None:
    mapped = map_client_error("SendMessage", SDKClientError("broken client"))

    assert isinstance(mapped, A2APeerProtocolError)
    assert mapped.error_code == "invalid_client_error"


def test_map_jsonrpc_error_variants() -> None:
    invalid_params_error = FakeA2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32602, message="bad params", data={"field": "limit"}),
            id="req-1",
        )
    )
    internal_error = FakeA2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32603, message="internal"),
            id="req-2",
        )
    )
    generic_error = FakeA2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32000, message="generic"),
            id="req-3",
        )
    )

    mapped_invalid = map_jsonrpc_error(invalid_params_error)
    mapped_internal = map_jsonrpc_error(internal_error)
    mapped_generic = map_jsonrpc_error(generic_error)

    assert isinstance(mapped_invalid, A2APeerProtocolError)
    assert mapped_invalid.error_code == "invalid_params"
    assert mapped_invalid.code == -32602
    assert mapped_invalid.data == {"field": "limit"}
    assert isinstance(mapped_internal, A2AClientResetRequiredError)
    assert isinstance(mapped_generic, A2APeerProtocolError)
    assert mapped_generic.error_code == "peer_protocol_error"
    assert mapped_generic.data is None


def test_map_http_error_variants() -> None:
    auth_failed = map_http_error("SendMessage", FakeA2AClientHTTPError(401, "denied"))
    permission_denied = map_http_error("SendMessage", FakeA2AClientHTTPError(403, "forbidden"))
    unsupported = map_http_error("SendMessage", FakeA2AClientHTTPError(405, "nope"))
    reset = map_http_error("SendMessage", FakeA2AClientHTTPError(503, "busy"))
    unavailable = map_http_error("SendMessage", FakeA2AClientHTTPError(500, "boom"))

    assert isinstance(auth_failed, A2AAuthenticationError)
    assert isinstance(permission_denied, A2APermissionDeniedError)
    assert isinstance(unsupported, A2AUnsupportedOperationError)
    assert isinstance(reset, A2AClientResetRequiredError)
    assert isinstance(unavailable, A2AAgentUnavailableError)


def test_map_operation_error_transport_and_timeout_variants() -> None:
    timeout = map_operation_error("SendMessage", httpx.ReadTimeout("timed out"))
    unavailable = map_operation_error("SendMessage", httpx.ConnectError("down"))

    assert isinstance(timeout, A2ATimeoutError)
    assert isinstance(unavailable, A2AAgentUnavailableError)


def test_map_agent_card_error_json_variant() -> None:
    mapped = map_agent_card_error(FakeA2AClientJSONError("invalid json"))

    assert isinstance(mapped, A2APeerProtocolError)
    assert mapped.error_code == "invalid_agent_card"


def test_map_agent_card_error_resolution_error_without_status_is_invalid_card() -> None:
    mapped = map_agent_card_error(AgentCardResolutionError("invalid json"))

    assert isinstance(mapped, A2APeerProtocolError)
    assert mapped.error_code == "invalid_agent_card"


def test_map_agent_card_error_resolution_error_with_status_uses_http_mapping() -> None:
    mapped = map_agent_card_error(AgentCardResolutionError("forbidden", status_code=403))

    assert isinstance(mapped, A2APermissionDeniedError)
    assert mapped.http_status == 403


@pytest.mark.parametrize(
    ("exc", "expected_type"),
    [
        pytest.param(httpx.ReadTimeout("timed out"), A2ATimeoutError, id="timeout"),
        pytest.param(httpx.ConnectError("down"), A2AAgentUnavailableError, id="transport"),
    ],
)
def test_map_agent_card_error_transport_variants(
    exc: httpx.TimeoutException | httpx.TransportError,
    expected_type: type[Exception],
) -> None:
    mapped = map_agent_card_error(exc)

    assert isinstance(mapped, expected_type)

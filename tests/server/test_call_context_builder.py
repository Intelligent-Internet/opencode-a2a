from starlette.requests import Request

from opencode_a2a.server.application import IdentityAwareCallContextBuilder


def _request(path: str, *, raw_path: bytes | None = None) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    req = Request(scope)
    req.state.user_identity = "opaque:test-id"
    req.state.user_auth_scheme = "bearer"
    req.state.user_credential_id = "cred-123"
    req.state.traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    req.state.tracestate = "vendor=value"
    req.state.trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    return req


def test_builder_sets_identity_for_non_stream_request():
    builder = IdentityAwareCallContextBuilder()
    context = builder.build(_request("/"))
    assert context.state.get("identity") == "opaque:test-id"
    assert context.user.is_authenticated is True
    assert context.user.user_name == "opaque:test-id"
    assert context.state.get("auth_scheme") == "bearer"
    assert context.state.get("credential_id") == "cred-123"
    assert context.state.get("traceparent") == (
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert context.state.get("tracestate") == "vendor=value"
    assert context.state.get("trace_id") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert context.state.get("a2a_streaming_request") is None


def test_builder_marks_rest_stream_request():
    builder = IdentityAwareCallContextBuilder()
    context = builder.build(_request("/message:stream"))
    assert context.state.get("identity") == "opaque:test-id"
    assert context.user.is_authenticated is True
    assert context.user.user_name == "opaque:test-id"
    assert context.state.get("auth_scheme") == "bearer"
    assert context.state.get("credential_id") == "cred-123"
    assert context.state.get("traceparent") == (
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert context.state.get("tracestate") == "vendor=value"
    assert context.state.get("trace_id") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert context.state.get("a2a_streaming_request") is True


def test_builder_marks_encoded_stream_request():
    builder = IdentityAwareCallContextBuilder()
    context = builder.build(_request("/message%3Astream", raw_path=b"/message%3Astream"))
    assert context.state.get("identity") == "opaque:test-id"
    assert context.user.is_authenticated is True
    assert context.user.user_name == "opaque:test-id"
    assert context.state.get("auth_scheme") == "bearer"
    assert context.state.get("credential_id") == "cred-123"
    assert context.state.get("traceparent") == (
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert context.state.get("tracestate") == "vendor=value"
    assert context.state.get("trace_id") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert context.state.get("a2a_streaming_request") is True

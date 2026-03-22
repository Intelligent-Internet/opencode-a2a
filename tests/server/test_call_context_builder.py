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
    return req


def test_builder_sets_identity_for_non_stream_request():
    builder = IdentityAwareCallContextBuilder()
    context = builder.build(_request("/"))
    assert context.state.get("identity") == "opaque:test-id"
    assert context.state.get("a2a_streaming_request") is None


def test_builder_marks_rest_stream_request():
    builder = IdentityAwareCallContextBuilder()
    context = builder.build(_request("/v1/message:stream"))
    assert context.state.get("identity") == "opaque:test-id"
    assert context.state.get("a2a_streaming_request") is True


def test_builder_marks_encoded_stream_request():
    builder = IdentityAwareCallContextBuilder()
    context = builder.build(_request("/v1/message%3Astream", raw_path=b"/v1/message%3Astream"))
    assert context.state.get("identity") == "opaque:test-id"
    assert context.state.get("a2a_streaming_request") is True

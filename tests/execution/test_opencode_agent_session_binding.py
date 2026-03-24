import asyncio
from typing import Any

import httpx
import pytest
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONRPCError
from a2a.types import JSONRPCError, JSONRPCErrorResponse, Task

from opencode_a2a.client.errors import (
    A2AClientResetRequiredError,
    A2APeerProtocolError,
    A2APermissionDeniedError,
    A2ATimeoutError,
    A2AUnsupportedOperationError,
)
from opencode_a2a.execution.executor import OpencodeAgentExecutor
from opencode_a2a.execution.tool_error_mapping import map_a2a_tool_exception
from opencode_a2a.opencode_upstream_client import OpencodeMessage
from tests.support.helpers import (
    DummyChatOpencodeUpstreamClient,
    DummyEventQueue,
    make_request_context,
)


@pytest.mark.asyncio
async def test_agent_prefers_metadata_shared_session_id() -> None:
    client = DummyChatOpencodeUpstreamClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = make_request_context(
        task_id="t-1",
        context_id="c-1",
        text="hello",
        metadata={"shared": {"session": {"id": "ses-bound"}}},
    )
    await executor.execute(ctx, q)

    assert client.created_sessions == 0
    assert client.sent_session_ids == ["ses-bound"]


@pytest.mark.asyncio
async def test_agent_passes_shared_model_override_to_upstream() -> None:
    client = DummyChatOpencodeUpstreamClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = make_request_context(
        task_id="t-model",
        context_id="c-model",
        text="hello",
        metadata={"shared": {"model": {"providerID": "google", "modelID": "gemini-2.5-flash"}}},
    )
    await executor.execute(ctx, q)

    assert client.sent_model_overrides == [{"providerID": "google", "modelID": "gemini-2.5-flash"}]


@pytest.mark.asyncio
async def test_agent_ignores_partial_shared_model_override() -> None:
    client = DummyChatOpencodeUpstreamClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx = make_request_context(
        task_id="t-model-invalid",
        context_id="c-model-invalid",
        text="hello",
        metadata={"shared": {"model": {"providerID": "google"}}},
    )
    await executor.execute(ctx, q)

    assert client.sent_model_overrides == [None]


@pytest.mark.asyncio
async def test_agent_caches_bound_session_id_for_followup_requests() -> None:
    client = DummyChatOpencodeUpstreamClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    ctx1 = make_request_context(
        task_id="t-1",
        context_id="c-1",
        text="hello",
        metadata={"shared": {"session": {"id": "ses-bound"}}},
    )
    await executor.execute(ctx1, q)

    ctx2 = make_request_context(
        task_id="t-2",
        context_id="c-1",
        text="follow",
        metadata=None,
    )
    await executor.execute(ctx2, q)

    assert client.created_sessions == 0
    assert client.sent_session_ids == ["ses-bound", "ses-bound"]


@pytest.mark.asyncio
async def test_agent_dedupes_concurrent_session_creates_per_context() -> None:
    class SlowCreateClient(DummyChatOpencodeUpstreamClient):
        async def create_session(
            self,
            title: str | None = None,
            *,
            directory: str | None = None,
        ) -> str:
            await asyncio.sleep(0.05)
            return await super().create_session(title=title, directory=directory)

    client = SlowCreateClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)

    async def run_one(task_id: str) -> None:
        q = DummyEventQueue()
        ctx = make_request_context(task_id=task_id, context_id="c-1", text="hi", metadata=None)
        await executor.execute(ctx, q)

    await asyncio.gather(run_one("t-1"), run_one("t-2"), run_one("t-3"))

    assert client.created_sessions == 1


@pytest.mark.asyncio
async def test_agent_uses_stable_fallback_message_id_when_upstream_missing_message_id() -> None:
    class MissingMessageIdClient(DummyChatOpencodeUpstreamClient):
        async def send_message(
            self,
            session_id: str,
            text: str | None = None,
            *,
            parts: list[dict[str, Any]] | None = None,
            directory: str | None = None,
            model_override: dict[str, str] | None = None,
            timeout_override: float | None = None,
            **kwargs: Any,
        ) -> OpencodeMessage:
            del text, parts, directory, model_override, timeout_override, kwargs
            self.sent_session_ids.append(session_id)
            return OpencodeMessage(
                text="echo:hello",
                session_id=session_id,
                message_id=None,
                raw={},
            )

    client = MissingMessageIdClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="t-fallback", context_id="c-fallback", text="hello"),
        q,
    )

    task = next(event for event in q.events if isinstance(event, Task))
    assert "message_id" not in task.metadata["shared"]["session"]
    assert task.status.message.message_id == "t-fallback:c-fallback:assistant"


@pytest.mark.asyncio
async def test_agent_includes_usage_in_non_stream_task_metadata() -> None:
    class UsageClient(DummyChatOpencodeUpstreamClient):
        async def send_message(
            self,
            session_id: str,
            text: str | None = None,
            *,
            parts: list[dict[str, Any]] | None = None,
            directory: str | None = None,
            model_override: dict[str, str] | None = None,
            timeout_override: float | None = None,
            **kwargs: Any,
        ) -> OpencodeMessage:
            del text, parts, directory, model_override, timeout_override, kwargs
            self.sent_session_ids.append(session_id)
            return OpencodeMessage(
                text="echo:hello",
                session_id=session_id,
                message_id="msg-usage",
                raw={
                    "info": {
                        "tokens": {
                            "input": 7,
                            "output": 3,
                            "reasoning": 0,
                            "cache": {"read": 0, "write": 0},
                        },
                        "cost": 0.0007,
                    }
                },
            )

    client = UsageClient()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False)
    q = DummyEventQueue()

    await executor.execute(
        make_request_context(task_id="t-usage", context_id="c-usage", text="hello"),
        q,
    )

    task = next(event for event in q.events if isinstance(event, Task))
    usage = task.metadata["shared"]["usage"]
    assert usage["input_tokens"] == 7
    assert usage["output_tokens"] == 3
    assert usage["total_tokens"] == 10
    assert "raw" not in usage


@pytest.mark.asyncio
async def test_agent_handles_a2a_call_tool(monkeypatch) -> None:
    from a2a.types import (
        Artifact,
        Part,
        Task,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TextPart,
    )

    from opencode_a2a.client import A2AClient

    class MockA2AClient:
        extract_text = staticmethod(A2AClient.extract_text)

        async def send_message(self, text: str):
            task = Task(
                id="remote-task",
                context_id="remote-ctx",
                status=TaskStatus(state=TaskState.working),
            )
            yield (
                task,
                TaskArtifactUpdateEvent(
                    task_id="remote-task",
                    context_id="remote-ctx",
                    artifact=Artifact(
                        artifact_id="artifact-1",
                        name="response",
                        parts=[Part(root=TextPart(text=f"remote response to {text}"))],
                    ),
                ),
            )

        async def close(self):
            pass

    class MockManager:
        class _BorrowedClient:
            async def __aenter__(self):
                return MockA2AClient()

            async def __aexit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

        def borrow_client(self, url: str):
            del url
            return self._BorrowedClient()

    client = DummyChatOpencodeUpstreamClient()
    manager = MockManager()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False, a2a_client_manager=manager)

    raw_response = {
        "parts": [
            {
                "type": "tool",
                "tool": "a2a_call",
                "callID": "call-1",
                "state": {
                    "status": "calling",
                    "input": {"url": "http://remote", "message": "hello remote"},
                },
            }
        ]
    }

    results = await executor._maybe_handle_tools(raw_response)
    assert results is not None
    assert len(results) == 1
    assert results[0]["call_id"] == "call-1"
    assert "remote response to hello remote" in results[0]["output"]


@pytest.mark.asyncio
async def test_execution_coordinator_handles_tool_loop() -> None:
    class ToolLoopClient(DummyChatOpencodeUpstreamClient):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        async def send_message(self, *args, **kwargs) -> OpencodeMessage:
            self.call_count += 1
            if self.call_count == 1:
                return OpencodeMessage(
                    text="call tool",
                    session_id="s1",
                    message_id="m1",
                    raw={
                        "parts": [
                            {
                                "type": "tool",
                                "tool": "a2a_call",
                                "callID": "c1",
                                "state": {
                                    "status": "calling",
                                    "input": {"url": "http://x", "message": "y"},
                                },
                            }
                        ]
                    },
                )
            return OpencodeMessage(text="done", session_id="s1", message_id="m2", raw={})

    class MockManager:
        class _BorrowedClient:
            async def __aenter__(self):
                mock_client = MagicMock()

                async def _send_message(_text: str):
                    task = Task(id="t", context_id="c", status=TaskStatus(state=TaskState.working))
                    yield (
                        task,
                        TaskArtifactUpdateEvent(
                            task_id="t",
                            context_id="c",
                            artifact=Artifact(
                                artifact_id="artifact-1",
                                name="response",
                                parts=[Part(root=TextPart(text="streamed tool output"))],
                            ),
                        ),
                    )

                mock_client.send_message = _send_message
                mock_client.extract_text = A2AClient.extract_text
                return mock_client

            async def __aexit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

        def borrow_client(self, url: str):
            del url
            return self._BorrowedClient()

    from unittest.mock import MagicMock

    from a2a.types import (
        Artifact,
        Part,
        Task,
        TaskArtifactUpdateEvent,
        TaskState,
        TaskStatus,
        TextPart,
    )

    from opencode_a2a.client import A2AClient

    client = ToolLoopClient()
    manager = MockManager()
    executor = OpencodeAgentExecutor(client, streaming_enabled=False, a2a_client_manager=manager)
    q = DummyEventQueue()

    await executor.execute(make_request_context(task_id="t1", context_id="c1", text="start"), q)

    assert client.call_count == 2
    task = next(event for event in q.events if isinstance(event, Task))
    assert task.status.message.parts[0].root.text == "done"


@pytest.mark.asyncio
async def test_agent_merges_streamed_a2a_tool_output() -> None:
    merged = OpencodeAgentExecutor._merge_streamed_tool_output("hello", "hello world")
    distinct = OpencodeAgentExecutor._merge_streamed_tool_output("hello world", "from peer")
    duplicate = OpencodeAgentExecutor._merge_streamed_tool_output("hello world", "world")

    assert merged == "hello world"
    assert distinct == "hello world\nfrom peer"
    assert duplicate == "hello world"


@pytest.mark.asyncio
async def test_agent_handles_a2a_call_tool_errors() -> None:
    from unittest.mock import MagicMock

    client = DummyChatOpencodeUpstreamClient()
    # No manager
    executor = OpencodeAgentExecutor(client, streaming_enabled=False, a2a_client_manager=None)

    raw_response = {
        "parts": [
            {
                "type": "tool",
                "tool": "a2a_call",
                "callID": "c1",
                "state": {"status": "calling", "input": {"url": "h", "message": "m"}},
            }
        ]
    }
    results = await executor._maybe_handle_tools(raw_response)
    assert results is not None
    assert results[0]["error_code"] == "a2a_client_manager_unavailable"
    assert "not available" in results[0]["error"]

    # Invalid input
    executor = OpencodeAgentExecutor(
        client, streaming_enabled=False, a2a_client_manager=MagicMock()
    )
    raw_response["parts"][0]["state"]["input"] = "invalid"
    results = await executor._maybe_handle_tools(raw_response)
    assert results is not None
    assert results[0]["error_code"] == "a2a_invalid_input"
    assert "Invalid a2a_call input" in results[0]["error"]

    # Missing message
    raw_response["parts"][0]["state"]["input"] = {"url": "http://x"}
    results = await executor._maybe_handle_tools(raw_response)
    assert results is not None
    assert results[0]["error_code"] == "a2a_missing_required_input"
    assert "Missing required a2a_call" in results[0]["error"]


@pytest.mark.asyncio
async def test_agent_maps_a2a_call_tool_auth_errors_to_stable_payload() -> None:
    class _AuthErrorStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise A2AClientHTTPError(401, "unauthorized")

    class MockA2AClient:
        def send_message(self, text: str):
            del text
            return _AuthErrorStream()

    class MockManager:
        class _BorrowedClient:
            async def __aenter__(self):
                return MockA2AClient()

            async def __aexit__(self, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

        def borrow_client(self, url: str):
            del url
            return self._BorrowedClient()

    client = DummyChatOpencodeUpstreamClient()
    executor = OpencodeAgentExecutor(
        client,
        streaming_enabled=False,
        a2a_client_manager=MockManager(),
    )

    results = await executor._maybe_handle_tools(
        {
            "parts": [
                {
                    "type": "tool",
                    "tool": "a2a_call",
                    "callID": "c-auth",
                    "state": {
                        "status": "calling",
                        "input": {"url": "http://remote", "message": "hello"},
                    },
                }
            ]
        }
    )

    assert results is not None
    assert results[0]["error_code"] == "a2a_peer_auth_failed"
    assert results[0]["error"] == "Authentication failed when calling remote A2A peer"
    assert results[0]["error_meta"]["http_status"] == 401


def test_map_a2a_tool_exception_protocol_and_unavailable_variants() -> None:
    rpc_error = A2AClientJSONRPCError(
        JSONRPCErrorResponse(
            error=JSONRPCError(code=-32602, message="bad params"),
            id="req-1",
        )
    )
    protocol_payload = map_a2a_tool_exception(rpc_error)
    unavailable_payload = map_a2a_tool_exception(httpx.ConnectError("down"))
    invalid_card_payload = map_a2a_tool_exception(
        A2APeerProtocolError(
            "bad card",
            error_code="invalid_agent_card",
        )
    )

    assert protocol_payload["error_code"] == "a2a_peer_protocol_error"
    assert protocol_payload["error_meta"]["rpc_code"] == -32602
    assert unavailable_payload["error_code"] == "a2a_unavailable"
    assert invalid_card_payload["error_code"] == "a2a_invalid_agent_card"


def test_map_a2a_tool_exception_additional_variants() -> None:
    permission_payload = map_a2a_tool_exception(A2APermissionDeniedError("denied"))
    timeout_payload = map_a2a_tool_exception(A2ATimeoutError("slow"))
    unsupported_payload = map_a2a_tool_exception(A2AUnsupportedOperationError("unsupported"))
    reset_payload = map_a2a_tool_exception(A2AClientResetRequiredError("retry"))
    generic_payload = map_a2a_tool_exception(RuntimeError("boom"))

    assert permission_payload["error_code"] == "a2a_peer_permission_denied"
    assert timeout_payload["error_code"] == "a2a_timeout"
    assert unsupported_payload["error_code"] == "a2a_unsupported_operation"
    assert reset_payload["error_code"] == "a2a_retryable_unavailable"
    assert generic_payload["error_code"] == "a2a_call_failed"

import pytest
from a2a.types import Part, TaskState

from opencode_a2a.a2a_utils import make_data_part
from opencode_a2a.execution.executor import OpencodeAgentExecutor
from opencode_a2a.opencode_upstream_client import OpencodeMessage
from tests.support.helpers import DummyEventQueue, make_request_context_with_parts
from tests.support.settings import make_settings


class RecordingMultipartClient:
    def __init__(self) -> None:
        self.stream_timeout = None
        self.directory = "/tmp/workspace"
        self.settings = make_settings(
            test_bearer_token="test",
            opencode_base_url="http://localhost",
        )
        self.created_titles: list[str | None] = []
        self.sent_calls: list[dict] = []

    async def close(self) -> None:
        return None

    async def create_session(
        self,
        title: str | None = None,
        *,
        directory: str | None = None,
    ) -> str:
        del directory
        self.created_titles.append(title)
        return "ses-1"

    async def send_message(
        self,
        session_id: str,
        text: str | None = None,
        *,
        parts: list[dict] | None = None,
        directory: str | None = None,
        model_override: dict[str, str] | None = None,
        timeout_override=None,  # noqa: ANN001
    ) -> OpencodeMessage:
        self.sent_calls.append(
            {
                "session_id": session_id,
                "text": text,
                "parts": parts,
                "directory": directory,
                "model_override": model_override,
                "timeout_override": timeout_override,
            }
        )
        return OpencodeMessage(
            text="processed",
            session_id=session_id,
            message_id="msg-1",
            raw={},
        )

    async def stream_events(self, stop_event=None, *, directory: str | None = None):  # noqa: ANN001
        del stop_event, directory
        for _ in ():
            yield {}

    async def remember_interrupt_request(self, **_kwargs) -> None:
        return None

    async def resolve_interrupt_request(self, request_id: str):
        del request_id
        return "missing", None

    async def resolve_interrupt_session(self, request_id: str) -> str | None:
        del request_id
        return None

    async def discard_interrupt_request(self, request_id: str) -> None:
        del request_id


@pytest.mark.asyncio
async def test_execute_forwards_text_and_file_parts() -> None:
    client = RecordingMultipartClient()
    executor = OpencodeAgentExecutor(client=client, streaming_enabled=False)
    queue = DummyEventQueue()
    context = make_request_context_with_parts(
        task_id="task-1",
        context_id="ctx-1",
        parts=[
            Part(text="Describe this file"),
            Part(raw=b"hello", filename="note.txt", media_type="text/plain"),
        ],
    )

    await executor.execute(context, queue)

    assert client.sent_calls == [
        {
            "session_id": "ses-1",
            "text": "Describe this file",
            "parts": [
                {"type": "text", "text": "Describe this file"},
                {
                    "type": "file",
                    "url": "data:text/plain;base64,aGVsbG8=",
                    "mime": "text/plain",
                    "filename": "note.txt",
                },
            ],
            "directory": client.directory,
            "model_override": None,
            "timeout_override": None,
        }
    ]
    assert client.created_titles == ["Describe this file"]
    assert queue.events[-1].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_execute_accepts_file_only_input() -> None:
    client = RecordingMultipartClient()
    executor = OpencodeAgentExecutor(client=client, streaming_enabled=False)
    queue = DummyEventQueue()
    context = make_request_context_with_parts(
        task_id="task-1",
        context_id="ctx-1",
        parts=[
            Part(
                url="file:///tmp/report.pdf",
                filename="report.pdf",
                media_type="application/pdf",
            )
        ],
    )

    await executor.execute(context, queue)

    assert client.sent_calls[0]["text"] is None
    assert client.sent_calls[0]["parts"] == [
        {
            "type": "file",
            "url": "file:///tmp/report.pdf",
            "mime": "application/pdf",
            "filename": "report.pdf",
        }
    ]
    assert client.created_titles == ["report.pdf"]
    assert queue.events[-1].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_execute_rejects_data_parts() -> None:
    client = RecordingMultipartClient()
    executor = OpencodeAgentExecutor(client=client, streaming_enabled=False)
    queue = DummyEventQueue()
    context = make_request_context_with_parts(
        task_id="task-1",
        context_id="ctx-1",
        parts=[make_data_part({"kind": "json", "value": 1})],
    )

    await executor.execute(context, queue)

    assert client.sent_calls == []
    task = queue.events[-1]
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert "structured data is not supported" in (
        getattr(task.status.message.parts[0], "text", None)
        or getattr(getattr(task.status.message.parts[0], "root", None), "text", "")
    )

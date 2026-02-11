from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events.event_queue import EventQueue

from opencode_a2a.agent import OpencodeAgentExecutor
from opencode_a2a.config import Settings
from opencode_a2a.opencode_client import OpencodeClient


@pytest.fixture
def mock_client():
    settings = Settings(
        A2A_BEARER_TOKEN="test",
        OPENCODE_BASE_URL="http://localhost",
        OPENCODE_DIRECTORY="/tmp/workspace",
        A2A_ALLOW_DIRECTORY_OVERRIDE=True,
    )

    client = OpencodeClient(settings)
    return client


def test_resolve_and_validate_directory_valid(mock_client):
    executor = OpencodeAgentExecutor(mock_client, streaming_enabled=False)

    # Setup mock workspace
    base_dir = Path("/tmp/workspace").resolve()

    # Valid subpath
    requested = "/tmp/workspace/project1"
    resolved = executor._resolve_and_validate_directory(requested)
    assert resolved == str(Path(requested).resolve())

    # Valid base path
    resolved = executor._resolve_and_validate_directory("/tmp/workspace")
    assert resolved == str(base_dir)

    # Relative path should be resolved against workspace root, not process cwd.
    resolved = executor._resolve_and_validate_directory("project2/sub")
    assert resolved == str((base_dir / "project2/sub").resolve())


def test_resolve_and_validate_directory_traversal(mock_client):
    executor = OpencodeAgentExecutor(mock_client, streaming_enabled=False)

    # Attempt traversal
    with pytest.raises(ValueError, match="outside the allowed workspace"):
        executor._resolve_and_validate_directory("/tmp/workspace/../secret")

    with pytest.raises(ValueError, match="outside the allowed workspace"):
        executor._resolve_and_validate_directory("/etc/passwd")

    with pytest.raises(ValueError, match="outside the allowed workspace"):
        executor._resolve_and_validate_directory("../secret")


def test_resolve_and_validate_directory_override_disabled(mock_client):
    # Disable override
    mock_client._settings.a2a_allow_directory_override = False
    executor = OpencodeAgentExecutor(mock_client, streaming_enabled=False)

    # Deny different path
    with pytest.raises(ValueError, match="override is disabled"):
        executor._resolve_and_validate_directory("/tmp/workspace/other")

    # Allow same path (resolved)
    resolved = executor._resolve_and_validate_directory("/tmp/workspace/./")
    assert resolved == str(Path("/tmp/workspace").resolve())


@pytest.mark.asyncio
async def test_execute_with_invalid_directory(mock_client):
    executor = OpencodeAgentExecutor(mock_client, streaming_enabled=False)
    event_queue = AsyncMock(spec=EventQueue)

    context = MagicMock(spec=RequestContext)
    context.task_id = "task-1"
    context.context_id = "ctx-1"
    context.metadata = {"directory": "/etc"}  # Illegal
    context.call_context = None

    await executor.execute(context, event_queue)

    # Verify error emission
    event_queue.enqueue_event.assert_called()
    from a2a.types import Task

    found_error = False
    for call in event_queue.enqueue_event.call_args_list:
        event = call[0][0]
        if isinstance(event, Task) and event.status.state.name == "failed":
            if "outside the allowed workspace" in str(event.status.message):
                found_error = True
    assert found_error

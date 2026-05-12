from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest import mock

import pytest
from a2a.types import TaskState
from pydantic import BaseModel, ValidationError, field_validator

from opencode_a2a import __version__, cli


@dataclass
class _FakeTextPart:
    text: str

    def HasField(self, name: str) -> bool:
        return name == "text"


@dataclass
class _FakeMessage:
    parts: list[_FakeTextPart]


@dataclass
class _FakeArtifact:
    artifact_id: str
    parts: list[_FakeTextPart]


@dataclass
class _FakeArtifactUpdate:
    artifact: _FakeArtifact
    append: bool


@dataclass
class _FakeStatus:
    state: TaskState
    message: str | None = None


@dataclass
class _FakeStatusUpdate:
    status: _FakeStatus | None


class _FakeEvent:
    def __init__(
        self,
        *,
        message: _FakeMessage | None = None,
        artifact_update: _FakeArtifactUpdate | None = None,
        status_update: _FakeStatusUpdate | None = None,
    ) -> None:
        self.message = message
        self.artifact_update = artifact_update
        self.status_update = status_update

    def HasField(self, name: str) -> bool:
        return getattr(self, name) is not None


def _message_event(*parts: str) -> _FakeEvent:
    return _FakeEvent(message=_FakeMessage(parts=[_FakeTextPart(text=part) for part in parts]))


def _artifact_event(artifact_id: str, text: str, *, append: bool = False) -> _FakeEvent:
    return _FakeEvent(
        artifact_update=_FakeArtifactUpdate(
            artifact=_FakeArtifact(artifact_id=artifact_id, parts=[_FakeTextPart(text=text)]),
            append=append,
        )
    )


def _failed_status_event(message: str) -> _FakeEvent:
    return _FakeEvent(
        status_update=_FakeStatusUpdate(
            status=_FakeStatus(state=TaskState.TASK_STATE_FAILED, message=message)
        )
    )


class _FakeA2AClient:
    def __init__(
        self,
        _agent_url: str,
        *,
        settings: object,
        events: list[_FakeEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.settings = settings
        self._events = events or []
        self._error = error
        self.closed = False

    async def send_message(self, _text: str) -> AsyncIterator[_FakeEvent]:
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    async def close(self) -> None:
        self.closed = True


def test_cli_help_does_not_require_runtime_settings(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert (
        "OpenCode A2A runtime for explicit service startup and peer calls. A2A Protocol 1.0 only."
    ) in help_text
    assert "   _ \\\\                    __|            |" in help_text
    assert "opencode-a2a <command> [arguments] [options]" in help_text
    assert "A2A_STATIC_AUTH_CREDENTIALS" in help_text
    assert "opencode serve --hostname 127.0.0.1 --port 4096" in help_text
    assert "A2A_CLIENT_BEARER_TOKEN=peer-token" in help_text
    assert "/.well-known/agent-card.json" in help_text
    assert "{call}" not in help_text
    assert "serve" in help_text
    assert "deploy-release" not in help_text
    assert "init-release-system" not in help_text
    assert "uninstall-instance" not in help_text
    serve_mock.assert_not_called()


@pytest.mark.parametrize("flag", ["-v", "--version"])
def test_cli_version_does_not_require_runtime_settings(
    flag: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main([flag])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_without_arguments_prints_help() -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        with mock.patch("opencode_a2a.cli.build_parser") as build_parser_mock:
            with mock.patch("opencode_a2a.cli.print_help_with_details") as print_help_mock:
                parser = mock.MagicMock()
                build_parser_mock.return_value = parser

                assert cli.main([]) == 0

    print_help_mock.assert_called_once_with(parser)
    serve_mock.assert_not_called()


def test_cli_serve_subcommand_runs_service() -> None:
    with mock.patch("opencode_a2a.cli.validate_serve_configuration", return_value=[]):
        with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
            assert cli.main(["serve"]) == 0

    serve_mock.assert_called_once_with()


def test_cli_serve_subcommand_with_invalid_configuration_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch(
        "opencode_a2a.cli.validate_serve_configuration",
        return_value=["Configure runtime authentication via A2A_STATIC_AUTH_CREDENTIALS"],
    ):
        with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
            assert cli.main(["serve"]) == 0

    help_text = capsys.readouterr().out
    assert "Run the OpenCode A2A service. A2A Protocol 1.0 only." in help_text
    assert "configuration errors:" in help_text
    assert "Configure runtime authentication via A2A_STATIC_AUTH_CREDENTIALS" in help_text
    serve_mock.assert_not_called()


def test_cli_call_without_required_arguments_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        assert cli.main(["call"]) == 0

    help_text = capsys.readouterr().out
    assert "Call an A2A agent using the A2A protocol. A2A Protocol 1.0 only." in help_text
    assert "A2A_CLIENT_BEARER_TOKEN=peer-token" in help_text
    assert "Service base URLs also work, but card URLs are the preferred example form." in help_text
    serve_mock.assert_not_called()


def test_cli_call_with_partial_required_arguments_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        assert cli.main(["call", "http://agent.example.com"]) == 0

    help_text = capsys.readouterr().out
    assert "usage: opencode-a2a call" in help_text
    assert "Agent card URL or service base URL of the agent to call." in help_text
    serve_mock.assert_not_called()


def test_cli_call_help_is_not_intercepted_when_explicit_flag_is_present() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["call", "--help"])

    assert excinfo.value.code == 0


def test_cli_call_rejects_bearer_flag() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["call", "http://agent.example.com", "hello", "--token", "peer-token"])

    assert excinfo.value.code == 2


def test_cli_call_rejects_basic_flag() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["call", "http://agent.example.com", "hello", "--basic", "user:pass"])

    assert excinfo.value.code == 2


class _DemoSettingsModel(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def _validate_token(cls, value: str) -> str:
        raise ValueError("missing token")


def test_validate_serve_configuration_formats_validation_errors() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _DemoSettingsModel(token="placeholder")

    with mock.patch("opencode_a2a.cli.Settings", side_effect=excinfo.value):
        assert cli.validate_serve_configuration() == ["token: missing token"]


@pytest.mark.asyncio
async def test_run_call_renders_incremental_artifacts_without_duplication(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = object()
    fake_client = _FakeA2AClient(
        "http://agent.example.com",
        settings=settings,
        events=[
            _message_event("hello "),
            _artifact_event("artifact-1", "abc"),
            _artifact_event("artifact-1", "abcdef"),
            _artifact_event("artifact-1", "abcdef"),
            _artifact_event("artifact-1", "!", append=True),
        ],
    )

    with mock.patch("opencode_a2a.cli.load_settings", return_value=settings):
        with mock.patch("opencode_a2a.cli.A2AClient", return_value=fake_client):
            assert await cli.run_call("http://agent.example.com", "hello") == 0

    assert capsys.readouterr().out == "hello abcdef!\n"
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_run_call_prints_failed_status_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_client = _FakeA2AClient(
        "http://agent.example.com",
        settings=object(),
        events=[_failed_status_event("task failed")],
    )

    with mock.patch("opencode_a2a.cli.load_settings", return_value=object()):
        with mock.patch("opencode_a2a.cli.A2AClient", return_value=fake_client):
            assert await cli.run_call("http://agent.example.com", "hello") == 0

    assert "[Failed] task failed" in capsys.readouterr().out
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_run_call_reports_errors_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    fake_client = _FakeA2AClient(
        "http://agent.example.com",
        settings=object(),
        error=RuntimeError("boom"),
    )

    with mock.patch("opencode_a2a.cli.load_settings", return_value=object()):
        with mock.patch("opencode_a2a.cli.A2AClient", return_value=fake_client):
            assert await cli.run_call("http://agent.example.com", "hello") == 1

    captured = capsys.readouterr()
    assert "[Error] boom" in captured.err
    assert fake_client.closed is True

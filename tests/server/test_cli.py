from __future__ import annotations

from unittest import mock

import pytest

from opencode_a2a import __version__, cli


def test_cli_help_does_not_require_runtime_settings(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert (
        "OpenCode A2A runtime for explicit service startup and peer calls. A2A Protocol 1.0 only."
    ) in help_text
    assert "___                    ____" in help_text
    assert "| | | | '_ \\ / _ \\ '_ \\| |   / _ \\ / _` |/ _ \\_____ / _ \\" in help_text
    assert "opencode-a2a <command> [arguments] [options]" in help_text
    assert "A2A_STATIC_AUTH_CREDENTIALS" in help_text
    assert "opencode serve --hostname 127.0.0.1 --port 4096" in help_text
    assert "A2A_CLIENT_BEARER_TOKEN=peer-token" in help_text
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
    serve_mock.assert_not_called()


def test_cli_call_with_partial_required_arguments_prints_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        assert cli.main(["call", "http://agent.example.com"]) == 0

    help_text = capsys.readouterr().out
    assert "usage: opencode-a2a call" in help_text
    assert "Text message to send." in help_text
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

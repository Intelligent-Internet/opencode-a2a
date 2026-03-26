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
    assert "Run without a subcommand to start the service." in help_text
    assert "{call}" in help_text
    assert "serve" not in help_text
    assert "deploy-release" not in help_text
    assert "init-release-system" not in help_text
    assert "uninstall-instance" not in help_text
    serve_mock.assert_not_called()


def test_cli_serve_subcommand_is_rejected() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["serve"])

    assert excinfo.value.code == 2


def test_cli_version_does_not_require_runtime_settings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_defaults_to_serve_when_no_subcommand() -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        assert cli.main([]) == 0

    serve_mock.assert_called_once_with()


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

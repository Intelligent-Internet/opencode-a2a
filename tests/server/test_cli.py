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
    assert "serve" in help_text
    assert "deploy-release" not in help_text
    assert "init-release-system" not in help_text
    assert "uninstall-instance" not in help_text
    serve_mock.assert_not_called()


def test_cli_serve_help_exposes_runtime_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["serve", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "Start the OpenCode A2A runtime using environment-based settings." in help_text


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


def test_cli_serve_subcommand_invokes_runtime() -> None:
    with mock.patch("opencode_a2a.cli.serve_main") as serve_mock:
        assert cli.main(["serve"]) == 0

    serve_mock.assert_called_once_with()

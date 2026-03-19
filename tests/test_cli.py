from __future__ import annotations

from importlib import resources
from unittest import mock

import pytest

from opencode_a2a_server import __version__, cli


def test_cli_help_does_not_require_runtime_settings(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("opencode_a2a_server.cli.serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--help"])

    assert excinfo.value.code == 0
    assert "serve" in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_version_does_not_require_runtime_settings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with mock.patch("opencode_a2a_server.cli.serve_main") as serve_mock:
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out
    serve_mock.assert_not_called()


def test_cli_defaults_to_serve_when_no_subcommand() -> None:
    with mock.patch("opencode_a2a_server.cli.serve_main") as serve_mock:
        assert cli.main([]) == 0

    serve_mock.assert_called_once_with()


def test_cli_serve_subcommand_invokes_runtime() -> None:
    with mock.patch("opencode_a2a_server.cli.serve_main") as serve_mock:
        assert cli.main(["serve"]) == 0

    serve_mock.assert_called_once_with()


def test_cli_init_release_system_subcommand_invokes_packaged_script() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert cli.main(["init-release-system"]) == 0

    run_mock.assert_called_once_with("init_release_system.sh", [])


def test_cli_deploy_release_subcommand_invokes_packaged_script() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert cli.main(["deploy-release", "project=alpha"]) == 0

    run_mock.assert_called_once_with("deploy_release.sh", ["project=alpha"])


def test_cli_uninstall_subcommand_invokes_packaged_script() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert cli.main(["uninstall-instance", "project=alpha"]) == 0

    run_mock.assert_called_once_with("uninstall.sh", ["project=alpha"])


def test_cli_packages_release_scripts_as_assets() -> None:
    assets_root = resources.files("opencode_a2a_server.assets").joinpath("scripts")
    assert assets_root.joinpath("init_release_system.sh").is_file()
    assert assets_root.joinpath("deploy_release.sh").is_file()
    assert assets_root.joinpath("uninstall.sh").is_file()

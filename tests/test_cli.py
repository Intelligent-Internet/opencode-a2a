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


def test_cli_init_release_system_help_exposes_release_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["init-release-system", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--release-version" in help_text
    assert "--release-root" in help_text
    assert "--tool-dir" in help_text
    assert "admin-oriented and optional" in help_text


def test_cli_deploy_release_help_exposes_flag_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["deploy-release", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--project" in help_text
    assert "--service-user" in help_text
    assert "--a2a-port" in help_text
    assert "--release-version" not in help_text
    assert "Secrets such as GH_TOKEN" in help_text
    assert "service account" in help_text
    assert "base" in help_text
    assert "directories must be prepared" in help_text


def test_cli_uninstall_help_exposes_flag_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["uninstall-instance", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--project" in help_text
    assert "--confirm" in help_text
    assert "Legacy key=value arguments are still accepted" in help_text


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


def test_cli_init_release_system_subcommand_invokes_packaged_script_with_env_overrides() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert (
            cli.main(
                [
                    "init-release-system",
                    "--release-version",
                    "0.2.1",
                    "--release-root",
                    "/opt/opencode-a2a-release",
                    "--data-root",
                    "/data/opencode-a2a",
                ]
            )
            == 0
        )

    run_mock.assert_called_once_with(
        "init_release_system.sh",
        [],
        env_overrides={
            "A2A_RELEASE_VERSION": "0.2.1",
            "A2A_RELEASE_ROOT": "/opt/opencode-a2a-release",
            "DATA_ROOT": "/data/opencode-a2a",
        },
    )


def test_cli_deploy_release_subcommand_supports_legacy_key_value_args() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert cli.main(["deploy-release", "project=alpha", "service_user=svc-alpha"]) == 0

    run_mock.assert_called_once_with(
        "deploy_release.sh",
        ["project=alpha", "service_user=svc-alpha"],
    )


def test_cli_deploy_release_subcommand_maps_flags_to_key_value_args() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert (
            cli.main(
                [
                    "deploy-release",
                    "--project",
                    "alpha",
                    "--service-user",
                    "svc-alpha",
                    "--service-group",
                    "opencode",
                    "--a2a-port",
                    "8010",
                    "--a2a-host",
                    "127.0.0.1",
                    "--a2a-enable-session-shell",
                    "--a2a-strict-isolation",
                    "--no-opencode-lsp",
                    "--force-restart",
                ]
            )
            == 0
        )

    run_mock.assert_called_once_with(
        "deploy_release.sh",
        [
            "project=alpha",
            "service_user=svc-alpha",
            "service_group=opencode",
            "a2a_port=8010",
            "a2a_host=127.0.0.1",
            "a2a_enable_session_shell=true",
            "a2a_strict_isolation=true",
            "opencode_lsp=false",
            "force_restart=true",
        ],
    )


def test_cli_uninstall_subcommand_supports_legacy_key_value_args() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert cli.main(["uninstall-instance", "project=alpha"]) == 0

    run_mock.assert_called_once_with("uninstall.sh", ["project=alpha"])


def test_cli_uninstall_subcommand_maps_flags_to_key_value_args() -> None:
    with mock.patch("opencode_a2a_server.cli._run_packaged_script", return_value=0) as run_mock:
        assert (
            cli.main(
                [
                    "uninstall-instance",
                    "--project",
                    "alpha",
                    "--data-root",
                    "/data/opencode-a2a",
                    "--confirm",
                    "UNINSTALL",
                ]
            )
            == 0
        )

    run_mock.assert_called_once_with(
        "uninstall.sh",
        [
            "project=alpha",
            "data_root=/data/opencode-a2a",
            "confirm=UNINSTALL",
        ],
    )


def test_cli_packages_release_scripts_as_assets() -> None:
    assets_root = resources.files("opencode_a2a_server.assets").joinpath("scripts")
    assert assets_root.joinpath("init_release_system.sh").is_file()
    assert assets_root.joinpath("deploy_release.sh").is_file()
    assert assets_root.joinpath("uninstall.sh").is_file()

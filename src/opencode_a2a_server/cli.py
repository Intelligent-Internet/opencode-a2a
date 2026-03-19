from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path

from . import __version__
from .app import main as serve_main


def _build_init_release_system_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    init_parser = subparsers.add_parser(
        "init-release-system",
        help="Optional admin-only host bootstrap for release deploys.",
        description=(
            "Optional admin-only host bootstrap for release deployments without a source checkout."
        ),
    )
    init_parser.add_argument(
        "--release-version",
        help="Install or refresh an exact published opencode-a2a-server package version.",
    )
    init_parser.add_argument(
        "--release-root",
        help="Shared release runtime root.",
    )
    init_parser.add_argument(
        "--tool-dir",
        help="uv tool runtime directory for the released CLI.",
    )
    init_parser.add_argument(
        "--tool-bin-dir",
        help="Directory where the released CLI executable is linked.",
    )
    init_parser.add_argument(
        "--deploy-helper-dir",
        help="Directory that stores packaged runtime helper scripts.",
    )
    init_parser.add_argument(
        "--opencode-core-dir",
        help="Shared OpenCode core directory.",
    )
    init_parser.add_argument(
        "--uv-python-dir",
        help="Shared uv-managed Python installation pool.",
    )
    init_parser.add_argument(
        "--uv-python-dir-group",
        help="Group owner used for the shared uv Python pool.",
    )
    init_parser.add_argument(
        "--data-root",
        help="Shared project data root created during bootstrap.",
    )
    init_parser.epilog = (
        "This command is intentionally admin-oriented and optional. "
        "The preferred product boundary is a pre-provisioned runtime plus "
        "lightweight instance-level deploy commands."
    )


def _build_deploy_release_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    deploy_parser = subparsers.add_parser(
        "deploy-release",
        help="Deploy one release-based systemd instance.",
        description="Deploy one lightweight release-based OpenCode + A2A systemd instance.",
    )
    deploy_parser.add_argument("--project", required=True, help="Project instance name.")
    deploy_parser.add_argument(
        "--service-user",
        required=True,
        help="Existing Linux service user for the instance.",
    )
    deploy_parser.add_argument(
        "--service-group",
        help="Existing Linux service group for the instance. Defaults to the user's primary group.",
    )
    deploy_parser.add_argument("--data-root", help="Per-project deployment root.")
    deploy_parser.add_argument("--a2a-port", type=int, help="A2A listen port.")
    deploy_parser.add_argument("--a2a-host", help="A2A bind host.")
    deploy_parser.add_argument("--a2a-public-url", help="Public A2A URL advertised by the server.")
    deploy_parser.add_argument("--a2a-log-level", help="A2A server log level.")
    deploy_parser.add_argument(
        "--a2a-otel-instrumentation-enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable A2A SDK OTEL instrumentation.",
    )
    deploy_parser.add_argument(
        "--a2a-log-payloads",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable request/response payload logging.",
    )
    deploy_parser.add_argument("--a2a-log-body-limit", type=int, help="Payload log body limit.")
    deploy_parser.add_argument(
        "--a2a-max-request-body-bytes",
        type=int,
        help="Maximum accepted request body size in bytes.",
    )
    deploy_parser.add_argument(
        "--a2a-cancel-abort-timeout-seconds",
        type=float,
        help="Timeout used when aborting cancellation flows.",
    )
    deploy_parser.add_argument(
        "--a2a-enable-session-shell",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable high-risk opencode.sessions.shell support.",
    )
    deploy_parser.add_argument(
        "--a2a-strict-isolation",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Apply stricter systemd isolation for shell-enabled instances.",
    )
    deploy_parser.add_argument(
        "--a2a-systemd-tasks-max",
        type=int,
        help="systemd TasksMax for the A2A instance service.",
    )
    deploy_parser.add_argument(
        "--a2a-systemd-limit-nofile",
        type=int,
        help="systemd LimitNOFILE for the A2A instance service.",
    )
    deploy_parser.add_argument(
        "--a2a-systemd-memory-max",
        help="systemd MemoryMax for the A2A instance service.",
    )
    deploy_parser.add_argument(
        "--a2a-systemd-cpu-quota",
        help="systemd CPUQuota for the A2A instance service.",
    )
    deploy_parser.add_argument(
        "--deploy-healthcheck-timeout-seconds",
        type=int,
        help="Maximum readiness probe wait time.",
    )
    deploy_parser.add_argument(
        "--deploy-healthcheck-interval-seconds",
        type=int,
        help="Readiness probe polling interval.",
    )
    deploy_parser.add_argument("--opencode-provider-id", help="Default OpenCode provider id.")
    deploy_parser.add_argument("--opencode-model-id", help="Default OpenCode model id.")
    deploy_parser.add_argument(
        "--opencode-lsp",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable OpenCode LSP support.",
    )
    deploy_parser.add_argument("--opencode-log-level", help="OpenCode log level.")
    deploy_parser.add_argument(
        "--opencode-timeout", type=int, help="OpenCode request timeout in seconds."
    )
    deploy_parser.add_argument(
        "--opencode-timeout-stream",
        type=int,
        help="OpenCode streaming timeout in seconds.",
    )
    deploy_parser.add_argument(
        "--git-identity-name", help="Git identity name configured in the workspace."
    )
    deploy_parser.add_argument(
        "--git-identity-email", help="Git identity email configured in the workspace."
    )
    deploy_parser.add_argument(
        "--enable-secret-persistence",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Persist GH_TOKEN, A2A_BEARER_TOKEN, and provider keys into root-only secret files.",
    )
    deploy_parser.add_argument(
        "--force-restart",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force a systemd restart even when units are already active.",
    )
    deploy_parser.epilog = (
        "Secrets such as GH_TOKEN, A2A_BEARER_TOKEN, and provider API keys remain "
        "environment-only inputs. The host runtime, service account, and base "
        "directories must be prepared by the operator. Legacy key=value arguments "
        "are still accepted for compatibility wrappers, but flags are the preferred "
        "CLI contract."
    )


def _build_uninstall_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    uninstall_parser = subparsers.add_parser(
        "uninstall-instance",
        help="Preview or remove one deployed project instance.",
        description="Preview or remove one deployed project instance.",
    )
    uninstall_parser.add_argument("--project", required=True, help="Project instance name.")
    uninstall_parser.add_argument("--data-root", help="Per-project deployment root.")
    uninstall_parser.add_argument(
        "--confirm",
        choices=["UNINSTALL"],
        help="Apply the uninstall. Omit this flag for preview mode.",
    )
    uninstall_parser.epilog = (
        "Legacy key=value arguments are still accepted for compatibility wrappers, "
        "but flags are the preferred CLI contract."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode-a2a-server",
        description="OpenCode A2A server CLI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "serve",
        help="Start the A2A server using environment-based settings.",
    )
    _build_init_release_system_parser(subparsers)
    _build_deploy_release_parser(subparsers)
    _build_uninstall_parser(subparsers)
    return parser


def _assets_scripts_dir() -> resources.abc.Traversable:
    return resources.files("opencode_a2a_server.assets").joinpath("scripts")


def _run_packaged_script(
    script_name: str,
    args: Sequence[str],
    *,
    env_overrides: Mapping[str, str] | None = None,
) -> int:
    with resources.as_file(_assets_scripts_dir()) as scripts_dir:
        script_path = Path(scripts_dir) / script_name
        if not script_path.is_file():
            print(f"Packaged release asset not found: {script_name}", file=sys.stderr)
            return 1
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        completed = subprocess.run(
            ["bash", str(script_path), *args],
            check=False,
            env=env,
        )
        return completed.returncode


def _is_legacy_passthrough(command: str, args: Sequence[str]) -> bool:
    if command not in {"deploy-release", "uninstall-instance"}:
        return False
    return bool(args) and all("=" in arg and not arg.startswith("-") for arg in args)


def _append_key_value_arg(args: list[str], key: str, value: object | None) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    else:
        rendered = str(value)
    args.append(f"{key}={rendered}")


def _build_init_release_env(namespace: argparse.Namespace) -> dict[str, str]:
    env: dict[str, str] = {}
    mappings = {
        "A2A_RELEASE_VERSION": namespace.release_version,
        "A2A_RELEASE_ROOT": namespace.release_root,
        "A2A_TOOL_DIR": namespace.tool_dir,
        "A2A_TOOL_BIN_DIR": namespace.tool_bin_dir,
        "DEPLOY_HELPER_DIR": namespace.deploy_helper_dir,
        "OPENCODE_CORE_DIR": namespace.opencode_core_dir,
        "UV_PYTHON_DIR": namespace.uv_python_dir,
        "UV_PYTHON_DIR_GROUP": namespace.uv_python_dir_group,
        "DATA_ROOT": namespace.data_root,
    }
    for env_key, value in mappings.items():
        if value is not None:
            env[env_key] = str(value)
    return env


def _build_deploy_release_args(namespace: argparse.Namespace) -> list[str]:
    args: list[str] = []
    mappings: tuple[tuple[str, object | None], ...] = (
        ("project", namespace.project),
        ("service_user", namespace.service_user),
        ("service_group", namespace.service_group),
        ("data_root", namespace.data_root),
        ("a2a_port", namespace.a2a_port),
        ("a2a_host", namespace.a2a_host),
        ("a2a_public_url", namespace.a2a_public_url),
        ("a2a_log_level", namespace.a2a_log_level),
        ("a2a_otel_instrumentation_enabled", namespace.a2a_otel_instrumentation_enabled),
        ("a2a_log_payloads", namespace.a2a_log_payloads),
        ("a2a_log_body_limit", namespace.a2a_log_body_limit),
        ("a2a_max_request_body_bytes", namespace.a2a_max_request_body_bytes),
        (
            "a2a_cancel_abort_timeout_seconds",
            namespace.a2a_cancel_abort_timeout_seconds,
        ),
        ("a2a_enable_session_shell", namespace.a2a_enable_session_shell),
        ("a2a_strict_isolation", namespace.a2a_strict_isolation),
        ("a2a_systemd_tasks_max", namespace.a2a_systemd_tasks_max),
        ("a2a_systemd_limit_nofile", namespace.a2a_systemd_limit_nofile),
        ("a2a_systemd_memory_max", namespace.a2a_systemd_memory_max),
        ("a2a_systemd_cpu_quota", namespace.a2a_systemd_cpu_quota),
        (
            "deploy_healthcheck_timeout_seconds",
            namespace.deploy_healthcheck_timeout_seconds,
        ),
        (
            "deploy_healthcheck_interval_seconds",
            namespace.deploy_healthcheck_interval_seconds,
        ),
        ("opencode_provider_id", namespace.opencode_provider_id),
        ("opencode_model_id", namespace.opencode_model_id),
        ("opencode_lsp", namespace.opencode_lsp),
        ("opencode_log_level", namespace.opencode_log_level),
        ("opencode_timeout", namespace.opencode_timeout),
        ("opencode_timeout_stream", namespace.opencode_timeout_stream),
        ("git_identity_name", namespace.git_identity_name),
        ("git_identity_email", namespace.git_identity_email),
        ("enable_secret_persistence", namespace.enable_secret_persistence),
        ("force_restart", namespace.force_restart),
    )
    for key, value in mappings:
        _append_key_value_arg(args, key, value)
    return args


def _build_uninstall_args(namespace: argparse.Namespace) -> list[str]:
    args: list[str] = []
    _append_key_value_arg(args, "project", namespace.project)
    _append_key_value_arg(args, "data_root", namespace.data_root)
    _append_key_value_arg(args, "confirm", namespace.confirm)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not args:
        serve_main()
        return 0
    if _is_legacy_passthrough(args[0], args[1:]):
        if args[0] == "deploy-release":
            return _run_packaged_script("deploy_release.sh", args[1:])
        if args[0] == "uninstall-instance":
            return _run_packaged_script("uninstall.sh", args[1:])

    namespace = parser.parse_args(args)
    if namespace.command in {None, "serve"}:
        serve_main()
        return 0
    if namespace.command == "init-release-system":
        return _run_packaged_script(
            "init_release_system.sh",
            [],
            env_overrides=_build_init_release_env(namespace),
        )
    if namespace.command == "deploy-release":
        return _run_packaged_script(
            "deploy_release.sh",
            _build_deploy_release_args(namespace),
        )
    if namespace.command == "uninstall-instance":
        return _run_packaged_script("uninstall.sh", _build_uninstall_args(namespace))

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

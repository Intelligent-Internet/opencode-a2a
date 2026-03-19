from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from . import __version__
from .app import main as serve_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode-a2a-server",
        description="OpenCode A2A server CLI.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Start the A2A server using environment-based settings.")
    init_parser = subparsers.add_parser(
        "init-release-system",
        help="Prepare a host for release-based deployments.",
    )
    init_parser.add_argument(
        "args", nargs="*", help="Pass-through arguments for init-release-system."
    )

    deploy_parser = subparsers.add_parser(
        "deploy-release",
        help="Deploy one release-based systemd instance.",
    )
    deploy_parser.add_argument("args", nargs="*", help="Pass-through key=value deploy arguments.")

    uninstall_parser = subparsers.add_parser(
        "uninstall-instance",
        help="Preview or remove one deployed project instance.",
    )
    uninstall_parser.add_argument(
        "args", nargs="*", help="Pass-through key=value uninstall arguments."
    )
    return parser


def _assets_scripts_dir() -> resources.abc.Traversable:
    return resources.files("opencode_a2a_server.assets").joinpath("scripts")


def _run_packaged_script(script_name: str, args: Sequence[str]) -> int:
    with resources.as_file(_assets_scripts_dir()) as scripts_dir:
        script_path = Path(scripts_dir) / script_name
        if not script_path.is_file():
            print(f"Packaged release asset not found: {script_name}", file=sys.stderr)
            return 1
        completed = subprocess.run(
            ["bash", str(script_path), *args],
            check=False,
        )
        return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not args:
        serve_main()
        return 0

    namespace = parser.parse_args(args)
    if namespace.command in {None, "serve"}:
        serve_main()
        return 0
    if namespace.command == "init-release-system":
        return _run_packaged_script("init_release_system.sh", namespace.args)
    if namespace.command == "deploy-release":
        return _run_packaged_script("deploy_release.sh", namespace.args)
    if namespace.command == "uninstall-instance":
        return _run_packaged_script("uninstall.sh", namespace.args)

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

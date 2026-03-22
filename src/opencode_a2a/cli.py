from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .server.application import main as serve_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencode-a2a",
        description=(
            "OpenCode A2A runtime. Deployment supervision is intentionally left to the operator."
        ),
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
        help="Start the OpenCode A2A runtime using environment-based settings.",
        description="Start the OpenCode A2A runtime using environment-based settings.",
    )
    return parser


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

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

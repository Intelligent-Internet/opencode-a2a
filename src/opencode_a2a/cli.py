from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from a2a.types import TaskState

from . import __version__
from .client import A2AClient, load_settings
from .server.application import main as serve_main

CLI_BRAND_BANNER = (
    "  ___                   ____          _\n"
    " / _ \\ _ __   ___ _ __ / ___|___   __| | ___\n"
    "| | | | '_ \\ / _ \\ '_ \\\\___ / _ \\ / _` |/ _ \\\n"
    "| |_| | |_) |  __/ | | |__) | (_) | (_| |  __/\n"
    " \\___/| .__/ \\___|_| |_|____/ \\___/ \\__,_|\\___|\n"
    "      |_|\n"
    "    _    ____    _\n"
    "   / \\  |___ \\  / \\\n"
    "  / _ \\   __) |/ _ \\\n"
    " / ___ \\ / __// ___ \\\n"
    "/_/   \\_\\_____/_/   \\_\\\n"
    "                    A2A Runtime"
)
PROJECT_REPOSITORY_URL = "https://github.com/Intelligent-Internet/opencode-a2a"


class RootHelpFormatter(
    argparse.RawDescriptionHelpFormatter,
    argparse.ArgumentDefaultsHelpFormatter,
):
    """Preserve banner formatting while keeping argparse defaults."""


class TopLevelArgumentParser(argparse.ArgumentParser):
    """Drop the generated usage line from the top-level help output only."""

    def format_help(self) -> str:
        help_text = super().format_help()
        lines = help_text.splitlines(keepends=True)
        if lines and lines[0].startswith("usage:"):
            help_text = "".join(lines[1:]).lstrip("\n")
        return help_text.replace("\ncommands:\n  command\n", "\ncommands:\n", 1)


async def run_call(agent_url: str, text: str) -> int:
    settings = load_settings(os.environ)
    client = A2AClient(agent_url, settings=settings)

    try:
        async for event in client.send_message(text):
            if event.HasField("message"):
                for part in event.message.parts:
                    text_val = part.text if part.HasField("text") else None
                    if isinstance(text_val, str):
                        print(text_val, end="", flush=True)
            elif event.HasField("artifact_update"):
                artifact = event.artifact_update.artifact
                if artifact and artifact.parts:
                    for part in artifact.parts:
                        text_val = part.text if part.HasField("text") else None
                        if isinstance(text_val, str):
                            print(text_val, end="", flush=True)
            elif (
                event.HasField("status_update")
                and event.status_update.status
                and event.status_update.status.state == TaskState.TASK_STATE_FAILED
            ):
                print(f"\n[Failed] {event.status_update.status.message or ''}")
        print()  # New line after completion
    except Exception as exc:
        print(f"\n[Error] {exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = TopLevelArgumentParser(
        prog="opencode-a2a",
        description=(
            CLI_BRAND_BANNER
            + "\n\n"
            + f"repo: {PROJECT_REPOSITORY_URL}\n"
            + "uv tool install --upgrade opencode-a2a\n"
            + (
                "protocol: A2A 1.0 only; "
                "not compatible with legacy 0.3 clients, methods, or payloads\n\n"
            )
            + "OpenCode A2A runtime for explicit service startup and peer calls.\n"
            + "  opencode-a2a <command> [arguments] [options]"
        ),
        formatter_class=RootHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="show program's version number and exit",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="command",
        parser_class=argparse.ArgumentParser,
    )

    subparsers.add_parser(
        "serve",
        help="Run the A2A service.",
        description="Run the OpenCode A2A service.",
    )

    call_parser = subparsers.add_parser(
        "call",
        help="Call an A2A agent.",
        description="Call an A2A agent using the A2A protocol.",
    )
    call_parser.add_argument("agent_url", help="URL of the agent to call.")
    call_parser.add_argument("text", help="Text message to send.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not args:
        parser.print_help()
        return 0

    namespace = parser.parse_args(args)
    if namespace.command == "serve":
        serve_main()
        return 0

    if namespace.command == "call":
        return asyncio.run(run_call(namespace.agent_url, namespace.text))

    if namespace.command is None:
        parser.print_help()
        return 0

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

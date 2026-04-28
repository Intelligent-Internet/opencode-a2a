from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from a2a.types import TaskState
from pydantic import ValidationError

from . import __version__
from .client import A2AClient, load_settings
from .config import Settings
from .server.application import main as serve_main

CLI_BRAND_BANNER = (
    "  ___                    ____          _              _    ____     _    \n"
    " / _ \\ _ __   ___ _ __  / ___|___   __| | ___        / \\  |___ \\   / \\   \n"
    "| | | | '_ \\ / _ \\ '_ \\| |   / _ \\ / _` |/ _ \\_____ / _ \\   __) | / _ \\  \n"
    "| |_| | |_) |  __/ | | | |__| (_) | (_| |  __/_____/ ___ \\ / __/ / ___ \\ \n"
    " \\___/| .__/ \\___|_| |_|\\____\\___/ \\__,_|\\___|    /_/   \\_\\_____/_/   \\_\\\n"
    "      |_|                                                                "
)
PROJECT_REPOSITORY_URL = "https://github.com/Intelligent-Internet/opencode-a2a"
HELP_FLAGS = frozenset({"-h", "--help"})

ROOT_DESCRIPTION = (
    "OpenCode A2A runtime for explicit service startup and peer calls. "
    "A2A Protocol 1.0 only.\n"
    "  opencode-a2a <command> [arguments] [options]"
)

OPENCODE_SETUP_HELP = (
    "OpenCode upstream quick start:\n"
    "  opencode auth login\n"
    "  opencode models\n"
    "  opencode serve --hostname 127.0.0.1 --port 4096\n"
    "\n"
    "OpenCode note:\n"
    "  Configure provider auth and a default model before starting opencode serve.\n"
    "  If provider auth comes from environment variables, export them before launch."
)

SERVE_ENVIRONMENT_HELP = (
    "Serve required environment:\n"
    "  A2A_STATIC_AUTH_CREDENTIALS\n"
    "    JSON array with at least one enabled bearer/basic credential.\n"
    "\n"
    "Serve common environment:\n"
    "  OPENCODE_BASE_URL\n"
    "    Upstream opencode serve URL. Default: http://127.0.0.1:4096\n"
    "  A2A_HOST\n"
    "    Bind host. Default: 127.0.0.1\n"
    "  A2A_PORT\n"
    "    Bind port. Default: 8000\n"
    "  A2A_PUBLIC_URL\n"
    "    Public base URL advertised in the agent card. Default: http://127.0.0.1:8000\n"
    "  A2A_TASK_STORE_BACKEND\n"
    "    database or memory. Default: database\n"
    "  A2A_TASK_STORE_DATABASE_URL\n"
    "    SQLAlchemy database URL. Default: sqlite+aiosqlite:///./opencode-a2a.db\n"
    "  OPENCODE_WORKSPACE_ROOT\n"
    "    Workspace root exposed to OpenCode tool execution.\n"
    "\n"
    "Serve minimal example:\n"
    "  DEMO_BEARER_TOKEN=\"$(python3 -c 'import secrets; print(secrets.token_hex(24))')\"\n"
    "  A2A_STATIC_AUTH_CREDENTIALS="
    '\'[{"scheme":"bearer","token":"\'"${DEMO_BEARER_TOKEN}"\'","principal":"automation"}]\''
    " \\\n"
    "  OPENCODE_BASE_URL=http://127.0.0.1:4096 \\\n"
    "  opencode-a2a serve\n"
    "\n"
    "Serve durable SQLite example:\n"
    "  DEMO_BEARER_TOKEN=\"$(python3 -c 'import secrets; print(secrets.token_hex(24))')\"\n"
    "  A2A_STATIC_AUTH_CREDENTIALS="
    '\'[{"scheme":"bearer","token":"\'"${DEMO_BEARER_TOKEN}"\'","principal":"automation"}]\''
    " \\\n"
    "  OPENCODE_BASE_URL=http://127.0.0.1:4096 \\\n"
    "  A2A_TASK_STORE_DATABASE_URL=sqlite+aiosqlite:///./opencode-a2a.db \\\n"
    "  OPENCODE_WORKSPACE_ROOT=/abs/path/to/workspace \\\n"
    "  opencode-a2a serve"
)

CALL_HELP = (
    "Call examples:\n"
    "  A2A_CLIENT_BEARER_TOKEN=peer-token \\\n"
    '  opencode-a2a call http://other-agent:8000/.well-known/agent-card.json "How are you?"\n'
    "\n"
    '  A2A_CLIENT_BASIC_AUTH="user:pass" \\\n'
    '  opencode-a2a call http://other-agent:8000/.well-known/agent-card.json "How are you?"\n'
    "\n"
    "Call note:\n"
    "  Outbound peer credentials are read from environment variables only.\n"
    "  Service base URLs also work, but card URLs are the preferred example form."
)

ROOT_HELP_EPILOG = f"{OPENCODE_SETUP_HELP}\n\n{SERVE_ENVIRONMENT_HELP}\n\n{CALL_HELP}"
SERVE_HELP_EPILOG = f"{OPENCODE_SETUP_HELP}\n\n{SERVE_ENVIRONMENT_HELP}"
CALL_HELP_EPILOG = CALL_HELP


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


def _find_subparser(
    parser: argparse.ArgumentParser,
    name: str,
) -> argparse.ArgumentParser | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get(name)
    return None


def _format_settings_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for error in exc.errors(include_url=False):
        message = str(error.get("msg", "Invalid configuration"))
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        location = ".".join(str(part) for part in error.get("loc", ()) if str(part) != "__root__")
        errors.append(f"{location}: {message}" if location else message)
    return errors or [str(exc)]


def validate_serve_configuration() -> list[str]:
    try:
        Settings()
    except ValidationError as exc:
        return _format_settings_errors(exc)
    return []


def print_help_with_details(
    parser: argparse.ArgumentParser,
    *,
    errors: Sequence[str] = (),
) -> None:
    parser.print_help()
    if errors:
        print("\nconfiguration errors:")
        for error in errors:
            print(f"  - {error}")


def should_show_call_help(args: Sequence[str]) -> bool:
    if not args or args[0] != "call":
        return False
    if any(token in HELP_FLAGS for token in args[1:]):
        return False
    positional_count = sum(1 for token in args[1:] if not token.startswith("-"))
    return positional_count < 2


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
            + ROOT_DESCRIPTION
        ),
        formatter_class=RootHelpFormatter,
        epilog=ROOT_HELP_EPILOG,
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
        description="Run the OpenCode A2A service. A2A Protocol 1.0 only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=SERVE_HELP_EPILOG,
    )

    call_parser = subparsers.add_parser(
        "call",
        help="Call an A2A agent.",
        description="Call an A2A agent using the A2A protocol. A2A Protocol 1.0 only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CALL_HELP_EPILOG,
    )
    call_parser.add_argument(
        "agent_url",
        help="Agent card URL or service base URL of the agent to call.",
    )
    call_parser.add_argument("text", help="Text message to send.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    call_parser = _find_subparser(parser, "call")
    serve_parser = _find_subparser(parser, "serve")

    if not args:
        print_help_with_details(parser)
        return 0

    if should_show_call_help(args) and call_parser is not None:
        print_help_with_details(call_parser)
        return 0

    namespace = parser.parse_args(args)
    if namespace.command == "serve":
        configuration_errors = validate_serve_configuration()
        if configuration_errors and serve_parser is not None:
            print_help_with_details(serve_parser, errors=configuration_errors)
            return 0
        serve_main()
        return 0

    if namespace.command == "call":
        return asyncio.run(run_call(namespace.agent_url, namespace.text))

    if namespace.command is None:
        print_help_with_details(parser)
        return 0

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence

from . import __version__
from .server.application import main as serve_main


async def run_call(agent_url: str, text: str, token: str | None = None) -> int:
    from a2a.types import Message, TaskArtifactUpdateEvent, TaskStatusUpdateEvent

    from .client import A2AClient

    client = A2AClient(agent_url)
    metadata = {}
    if token:
        # Use Authorization header for bearer auth.
        metadata["authorization"] = f"Bearer {token}"

    try:
        async for event in client.send_message(text, metadata=metadata):
            if isinstance(event, tuple):
                _, update = event
                if isinstance(update, TaskArtifactUpdateEvent):
                    artifact = update.artifact
                    if artifact and artifact.parts:
                        for part in artifact.parts:
                            text_val = getattr(part.root, "text", None)
                            if isinstance(text_val, str):
                                print(text_val, end="", flush=True)
                elif isinstance(update, TaskStatusUpdateEvent):
                    if update.status and update.status.state == "failed":
                        print(f"\n[Failed] {update.status.message or ''}")
            elif isinstance(event, Message):
                for part in event.parts:
                    text_val = getattr(part.root, "text", None)
                    if isinstance(text_val, str):
                        print(text_val, end="", flush=True)
        print()  # New line after completion
    except Exception as exc:
        print(f"\n[Error] {exc}", file=sys.stderr)
        return 1
    finally:
        await client.close()
    return 0


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

    call_parser = subparsers.add_parser(
        "call",
        help="Call an A2A agent.",
        description="Call an A2A agent using the A2A protocol.",
    )
    call_parser.add_argument("agent_url", help="URL of the agent to call.")
    call_parser.add_argument("text", help="Text message to send.")
    call_parser.add_argument(
        "--token",
        help="Bearer token for authentication (can also use A2A_BEARER_TOKEN env).",
        default=os.environ.get("A2A_BEARER_TOKEN"),
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not args:
        serve_main()
        return 0

    namespace = parser.parse_args(args)
    if namespace.command == "serve":
        serve_main()
        return 0

    if namespace.command == "call":
        return asyncio.run(run_call(namespace.agent_url, namespace.text, namespace.token))

    if namespace.command is None:
        serve_main()
        return 0

    parser.error(f"Unknown command: {namespace.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

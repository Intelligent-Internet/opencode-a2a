from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
from collections import deque
from dataclasses import dataclass

from .config import Settings

_LISTENING_PATTERN = re.compile(r"opencode server listening on (?P<url>https?://\S+)")
_OUTPUT_BUFFER_LIMIT = 40


def _pick_managed_server_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _resolve_opencode_command(command: str) -> str:
    if os.path.sep in command:
        if os.path.exists(command):
            return command
        raise RuntimeError(f"Managed upstream command not found: {command}")
    resolved = shutil.which(command)
    if resolved:
        return resolved
    raise RuntimeError(
        f"Managed upstream command not found on PATH: {command}. "
        "Install opencode or set OPENCODE_COMMAND."
    )


@dataclass
class ManagedOpencodeServer:
    process: asyncio.subprocess.Process
    base_url: str
    _output_task: asyncio.Task[None]

    async def close(self) -> None:
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await self._output_task


async def _consume_process_output(
    stream: asyncio.StreamReader | None,
    *,
    ready_future: asyncio.Future[str],
    output_buffer: deque[str],
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if not text:
            continue
        output_buffer.append(text)
        match = _LISTENING_PATTERN.search(text)
        if match and not ready_future.done():
            ready_future.set_result(match.group("url"))


async def launch_managed_opencode_server(settings: Settings) -> ManagedOpencodeServer:
    host = settings.opencode_managed_server_host
    port = settings.opencode_managed_server_port or _pick_managed_server_port(host)
    command = _resolve_opencode_command(settings.opencode_command)
    cmd = [
        command,
        "serve",
        "--hostname",
        host,
        "--port",
        str(port),
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    ready_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    output_buffer: deque[str] = deque(maxlen=_OUTPUT_BUFFER_LIMIT)
    output_task = asyncio.create_task(
        _consume_process_output(
            process.stdout,
            ready_future=ready_future,
            output_buffer=output_buffer,
        )
    )
    try:
        base_url = await asyncio.wait_for(ready_future, timeout=settings.opencode_startup_timeout)
    except Exception as exc:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        await output_task
        buffered_output = "\n".join(output_buffer).strip() or "<no output>"
        raise RuntimeError(
            "Managed OpenCode upstream failed to become ready. "
            f"command={' '.join(cmd)} output={buffered_output}"
        ) from exc
    return ManagedOpencodeServer(
        process=process,
        base_url=base_url.rstrip("/"),
        _output_task=output_task,
    )

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from opencode_a2a_server.app import create_app
from opencode_a2a_server.opencode_upstream import (
    ManagedOpencodeServer,
    _resolve_opencode_command,
    launch_managed_opencode_server,
)
from tests.helpers import make_settings


class _DummyProcess:
    def __init__(self, lines: list[str], *, returncode: int | None = None) -> None:
        self.stdout = asyncio.StreamReader()
        for line in lines:
            self.stdout.feed_data(line.encode("utf-8"))
        self.stdout.feed_eof()
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_resolve_opencode_command_errors_when_not_found(monkeypatch) -> None:
    monkeypatch.setattr("opencode_a2a_server.opencode_upstream.shutil.which", lambda _: None)

    with pytest.raises(RuntimeError) as excinfo:
        _resolve_opencode_command("opencode")

    assert "OPENCODE_COMMAND" in str(excinfo.value)


@pytest.mark.asyncio
async def test_launch_managed_opencode_server_uses_listening_url(monkeypatch) -> None:
    dummy = _DummyProcess(
        [
            "Warning: OPENCODE_SERVER_PASSWORD is not set; server is unsecured.\n",
            "opencode server listening on http://127.0.0.1:40419\n",
        ]
    )
    captured: list[str] = []

    async def _fake_create_subprocess_exec(*cmd, **kwargs):  # noqa: ANN001
        del kwargs
        captured.extend(cmd)
        return dummy

    monkeypatch.setattr(
        "opencode_a2a_server.opencode_upstream._resolve_opencode_command",
        lambda command: command,
    )
    monkeypatch.setattr(
        "opencode_a2a_server.opencode_upstream._pick_managed_server_port",
        lambda host: 40419,
    )
    monkeypatch.setattr(
        "opencode_a2a_server.opencode_upstream.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    settings = make_settings(
        opencode_managed_server=True,
        opencode_command="opencode",
        opencode_startup_timeout=1.0,
    )

    handle = await launch_managed_opencode_server(settings)

    assert captured == ["opencode", "serve", "--hostname", "127.0.0.1", "--port", "40419"]
    assert handle.base_url == "http://127.0.0.1:40419"
    await handle.close()
    assert dummy.terminated is True


@pytest.mark.asyncio
async def test_launch_managed_opencode_server_surfaces_startup_failure(monkeypatch) -> None:
    dummy = _DummyProcess(["fatal: boom\n"], returncode=1)

    async def _fake_create_subprocess_exec(*_cmd, **_kwargs):  # noqa: ANN001
        return dummy

    monkeypatch.setattr(
        "opencode_a2a_server.opencode_upstream._resolve_opencode_command",
        lambda command: command,
    )
    monkeypatch.setattr(
        "opencode_a2a_server.opencode_upstream._pick_managed_server_port",
        lambda host: 40420,
    )
    monkeypatch.setattr(
        "opencode_a2a_server.opencode_upstream.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    settings = make_settings(
        opencode_managed_server=True,
        opencode_command="opencode",
        opencode_startup_timeout=0.01,
    )

    with pytest.raises(RuntimeError) as excinfo:
        await launch_managed_opencode_server(settings)

    assert "failed to become ready" in str(excinfo.value)
    assert "fatal: boom" in str(excinfo.value)


def test_create_app_manages_upstream_lifecycle(monkeypatch) -> None:
    import opencode_a2a_server.app as app_module

    calls: list[str] = []

    class _DummyHandle(ManagedOpencodeServer):
        def __init__(self) -> None:
            self.process = _DummyProcess([])
            self.base_url = "http://127.0.0.1:40421"
            self._output_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))

        async def close(self) -> None:  # type: ignore[override]
            calls.append("managed_close")

    async def _fake_launch(settings):  # noqa: ANN001
        assert settings.opencode_managed_server is True
        calls.append("launch")
        return _DummyHandle()

    async def _fake_rebind(self, base_url: str) -> None:  # noqa: ANN001
        calls.append(f"rebind:{base_url}")

    async def _fake_close(self) -> None:  # noqa: ANN001
        calls.append("client_close")

    monkeypatch.setattr(app_module, "launch_managed_opencode_server", _fake_launch)
    monkeypatch.setattr(app_module.OpencodeClient, "rebind_base_url", _fake_rebind)
    monkeypatch.setattr(app_module.OpencodeClient, "close", _fake_close)

    app = create_app(
        make_settings(
            a2a_bearer_token="test-token",
            opencode_managed_server=True,
        )
    )

    with TestClient(app):
        assert calls[:2] == ["launch", "rebind:http://127.0.0.1:40421"]

    assert calls == ["launch", "rebind:http://127.0.0.1:40421", "managed_close", "client_close"]

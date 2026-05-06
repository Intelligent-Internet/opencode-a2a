from __future__ import annotations

from unittest import mock

from tests.support.helpers import make_settings


def test_make_settings_ignores_environment_and_dotenv_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENCODE_BASE_URL=http://dotenv-upstream.test",
                "A2A_PUBLIC_URL=http://dotenv-public.test",
                "A2A_HOST=dotenv-host",
            ]
        ),
        encoding="utf-8",
    )

    with mock.patch.dict(
        "os.environ",
        {
            "OPENCODE_BASE_URL": "http://env-upstream.test",
            "A2A_PUBLIC_URL": "http://env-public.test",
            "A2A_HOST": "env-host",
        },
        clear=False,
    ):
        settings = make_settings()

    assert settings.opencode_base_url == "http://127.0.0.1:4096"
    assert settings.a2a_public_url == "http://127.0.0.1:8000"
    assert settings.a2a_host == "127.0.0.1"

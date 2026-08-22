from __future__ import annotations

from opencode_a2a.redact import (
    REDACTED_PATH_PLACEHOLDER,
    redact_absolute_paths,
    redact_paths_in_value,
)


def test_masks_posix_absolute_paths() -> None:
    text = (
        "FileNotFoundError: [Errno 2] No such file or directory: "
        "'/home/ubuntu/secret/data.txt'"
    )
    assert redact_absolute_paths(text) == (
        "FileNotFoundError: [Errno 2] No such file or directory: "
        f"'{REDACTED_PATH_PLACEHOLDER}'"
    )


def test_masks_multiple_paths() -> None:
    text = "paths /tmp/a and /var/log/opencode/app.log"
    assert redact_absolute_paths(text) == (
        f"paths {REDACTED_PATH_PLACEHOLDER} and {REDACTED_PATH_PLACEHOLDER}"
    )


def test_masks_windows_drive_paths() -> None:
    assert redact_absolute_paths(r"C:\Users\alice\secret.txt") == REDACTED_PATH_PLACEHOLDER
    assert redact_absolute_paths("C:/Users/alice/secret.txt") == REDACTED_PATH_PLACEHOLDER


def test_masks_unc_paths() -> None:
    assert redact_absolute_paths(r"\\server\share\data\file.txt") == REDACTED_PATH_PLACEHOLDER


def test_masks_file_url_paths() -> None:
    assert redact_absolute_paths("file:///tmp/secret/config.json") == REDACTED_PATH_PLACEHOLDER
    assert redact_absolute_paths("file://C:/tmp/secret.txt") == REDACTED_PATH_PLACEHOLDER


def test_preserves_remote_urls() -> None:
    text = "upstream https://example.com/path/to/file failed; local http://localhost:8000/a2a"
    assert redact_absolute_paths(text) == text


def test_preserves_relative_paths() -> None:
    text = "module src/opencode_a2a/redact.py and ./local and ../parent/file"
    assert redact_absolute_paths(text) == text


def test_preserves_api_route_tokens() -> None:
    text = "REST route /message:send failed; try /tasks/{id}:cancel or /message:stream"
    assert redact_absolute_paths(text) == text


def test_preserves_trailing_punctuation() -> None:
    assert redact_absolute_paths("/tmp/x.") == f"{REDACTED_PATH_PLACEHOLDER}."
    assert redact_absolute_paths("/tmp/x,") == f"{REDACTED_PATH_PLACEHOLDER},"


def test_redaction_is_idempotent() -> None:
    text = "error /home/u/x with https://example.com/a/b and C:\\Users\\a\\y"
    once = redact_absolute_paths(text)
    assert redact_absolute_paths(once) == once


def test_plain_text_unchanged() -> None:
    text = "hello world; method=send_message; code=-32001"
    assert redact_absolute_paths(text) == text


def test_redact_paths_in_value_recurses() -> None:
    value = {
        "directory": "/home/ubuntu/project",
        "nested": [
            {"path": r"C:\Users\alice\x"},
            {"url": "https://example.com/a"},
        ],
        "count": 3,
        "flag": True,
    }
    result = redact_paths_in_value(value)
    assert result["directory"] == REDACTED_PATH_PLACEHOLDER
    assert result["nested"][0]["path"] == REDACTED_PATH_PLACEHOLDER
    assert result["nested"][1]["url"] == "https://example.com/a"
    assert result["count"] == 3
    assert result["flag"] is True


def test_redact_paths_in_value_handles_tuples() -> None:
    result = redact_paths_in_value(("ok", "/tmp/x", ["/var/log/y"]))
    assert result == ("ok", REDACTED_PATH_PLACEHOLDER, [REDACTED_PATH_PLACEHOLDER])

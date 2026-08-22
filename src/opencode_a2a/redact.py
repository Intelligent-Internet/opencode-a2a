"""Deterministic masking of absolute filesystem paths in error-facing text.

Client-visible error messages (JSON-RPC/REST error bodies and streaming task
messages) can embed exception text that includes host-local absolute paths,
for example::

    FileNotFoundError: [Errno 2] No such file or directory: '/home/ubuntu/x'

This module replaces such paths with a fixed placeholder before the text
leaves the process, while leaving URLs, relative paths, and ordinary prose
untouched. The replacement is deterministic and idempotent.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED_PATH_PLACEHOLDER = "<redacted-path>"

# A path segment: word characters plus common path-safe punctuation
# (``- _ + @ %``), with dots allowed only between word characters so trailing
# sentence punctuation (``.``/``,``/``;``) is not swallowed by the match.
_SEGMENT = r"[\w@+%-]+(?:\.[\w@+%-]+)*"

# POSIX absolute paths: ``/a/b`` with at least one segment. The leading slash
# must not be preceded by a word character (which would make it a relative
# ``a/b`` fragment) or another slash or dot (which would make it part of a URL
# authority such as ``https://host/path``, a ``//host/path`` reference, or a
# ``../path`` traversal fragment). A match is not taken when it is immediately
# followed by a word character, ``:`` method suffix, ``/`` or ``{`` route
# template, so API route tokens such as ``/message:send`` or
# ``/tasks/{id}:cancel`` are preserved rather than mistaken for local paths.
_POSIX_ABSOLUTE_PATH = re.compile("(?<![\\\\/\\w.])(?:/" + _SEGMENT + ")+(?![\\w:/{])")

# Windows drive paths (``C:\\a\\b`` / ``C:/a/b``) and UNC paths
# (``\\\\server\\share\\path``). A drive letter must be followed by a
# separator and at least one segment.
_WINDOWS_ABSOLUTE_PATH = re.compile(
    rf"(?<![\\/\w])(?:[A-Za-z]:[\\/]|\\\\[\w@+%-]+[\\/]){_SEGMENT}(?:[\\/]{_SEGMENT})*"
)

# ``file://`` URIs embed an absolute local path (``file:///tmp/x`` or
# ``file://C:/tmp/x``) and are therefore treated as path leaks, unlike remote
# ``scheme://host/path`` URLs which are preserved.
_FILE_URL_PATH = re.compile(
    r"(?i)file://"
    r"(?:[A-Za-z]:[\\/]|[\w@+%-]+(?:[.:][\w@+%-]+)*/|/)"
    rf"(?:{_SEGMENT})(?:/{_SEGMENT})*"
)


def redact_absolute_paths(text: str) -> str:
    """Return ``text`` with absolute filesystem paths replaced by a placeholder.

    Both POSIX (``/a/b``) and Windows (``C:\\a\\b``, ``\\\\server\\share``)
    absolute paths are replaced. URLs (``scheme://host/path``), relative paths
    (``a/b``), and prose without absolute paths are left unchanged. ``file://``
    URIs and Windows paths are processed before POSIX paths so a
    drive-qualified local path is replaced as one unit rather than leaving the
    ``file://C:`` or ``C:`` prefix behind.
    """
    redacted = _FILE_URL_PATH.sub(REDACTED_PATH_PLACEHOLDER, text)
    redacted = _WINDOWS_ABSOLUTE_PATH.sub(REDACTED_PATH_PLACEHOLDER, redacted)
    return _POSIX_ABSOLUTE_PATH.sub(REDACTED_PATH_PLACEHOLDER, redacted)


def redact_paths_in_value(value: Any) -> Any:
    """Recursively redact absolute paths in every string leaf of ``value``.

    ``value`` is expected to be JSON-compatible (dict/list/tuple/str/scalar).
    Keys are kept as-is so error metadata remains machine-readable.
    """
    if isinstance(value, str):
        return redact_absolute_paths(value)
    if isinstance(value, list):
        return [redact_paths_in_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_paths_in_value(item) for item in value)
    if isinstance(value, dict):
        return {str(key): redact_paths_in_value(item) for key, item in value.items()}
    return value

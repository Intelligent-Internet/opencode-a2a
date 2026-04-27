from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any, Literal, TypedDict


class UnsupportedA2AInputError(ValueError):
    """Raised when an incoming A2A part cannot be mapped to OpenCode input."""


class OpencodeTextInputPart(TypedDict):
    type: Literal["text"]
    text: str


class OpencodeFileInputPart(TypedDict, total=False):
    type: Literal["file"]
    url: str
    mime: str
    filename: str


OpencodeInputPart = OpencodeTextInputPart | OpencodeFileInputPart


def extract_text_from_a2a_parts(parts: Any) -> str:
    normalized_parts = _normalize_parts(parts)
    if normalized_parts is None:
        return ""

    texts: list[str] = []
    for part in normalized_parts:
        if _part_kind(part) != "text":
            continue
        text = _part_text_value(part)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts).strip()


def summarize_a2a_parts(parts: Any) -> str | None:
    text = extract_text_from_a2a_parts(parts)
    if text:
        return text[:80]

    normalized_parts = _normalize_parts(parts)
    if normalized_parts is None:
        return None

    filenames: list[str] = []
    for part in normalized_parts:
        if _part_kind(part) != "file":
            continue
        name = _part_filename(part)
        if isinstance(name, str) and name.strip():
            filenames.append(name.strip())
        else:
            filenames.append("file")

    if not filenames:
        return None
    if len(filenames) == 1:
        return filenames[0]
    return ", ".join(filenames[:3])[:80]


def map_a2a_parts_to_opencode_parts(parts: Any) -> list[OpencodeInputPart]:
    normalized_parts = _normalize_parts(parts)
    if normalized_parts is None:
        return []

    mapped: list[OpencodeInputPart] = []
    for index, part in enumerate(normalized_parts):
        kind = _part_kind(part)

        if kind == "text":
            text = _part_text_value(part)
            if isinstance(text, str):
                mapped.append({"type": "text", "text": text})
            continue

        if kind == "file":
            mapped.append(_map_file_part(part, index=index))
            continue

        if kind == "data":
            raise UnsupportedA2AInputError(
                "request.parts["
                f"{index}"
                "] structured data is not supported; use text, raw, or url parts."
            )

        raise UnsupportedA2AInputError(
            f"request.parts[{index}] is not supported; only text, raw, or url parts are accepted."
        )

    return mapped


def _map_file_part(part: Any, *, index: int) -> OpencodeFileInputPart:
    raw_bytes = getattr(part, "raw", None)
    url = _normalize_string(getattr(part, "url", None))
    if isinstance(raw_bytes, bytes) and raw_bytes:
        mime = _normalize_string(getattr(part, "media_type", None)) or "application/octet-stream"
        name = _normalize_string(getattr(part, "filename", None))
        mapped: OpencodeFileInputPart = {
            "type": "file",
            "url": f"data:{mime};base64,{base64.b64encode(raw_bytes).decode('ascii')}",
            "mime": mime,
        }
        if name:
            mapped["filename"] = name
        return mapped
    if url:
        mime = _normalize_string(getattr(part, "media_type", None)) or "application/octet-stream"
        name = _normalize_string(getattr(part, "filename", None))
        mapped = {
            "type": "file",
            "url": url,
            "mime": mime,
        }
        if name:
            mapped["filename"] = name
        return mapped

    raise UnsupportedA2AInputError(
        f"request.parts[{index}] file input must contain either raw bytes or a url."
    )


def _part_kind(part: Any) -> str | None:
    if isinstance(getattr(part, "text", None), str) and getattr(part, "text", None):
        return "text"
    if isinstance(getattr(part, "raw", None), bytes) and getattr(part, "raw", None):
        return "file"
    if _normalize_string(getattr(part, "url", None)):
        return "file"
    data = getattr(part, "data", None)
    which_oneof = getattr(data, "WhichOneof", None)
    if callable(which_oneof) and which_oneof("kind") is not None:
        return "data"
    return None


def _part_text_value(part: Any) -> str | None:
    text = getattr(part, "text", None)
    if isinstance(text, str):
        return text
    return None


def _part_filename(part: Any) -> str | None:
    filename = _normalize_string(getattr(part, "filename", None))
    if filename:
        return filename
    return None


def _normalize_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized else None


def _normalize_parts(parts: Any) -> list[Any] | None:
    if not isinstance(parts, Sequence) or isinstance(parts, str | bytes | bytearray):
        return None
    return list(parts)

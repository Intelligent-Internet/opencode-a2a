from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Literal, TypedDict

from a2a.types import Part


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


def extract_text_from_a2a_parts(parts: Sequence[Part] | None) -> str:
    if not parts:
        return ""

    texts: list[str] = []
    for part in parts:
        if part.HasField("text") and part.text:
            texts.append(part.text)
    return "\n".join(texts).strip()


def summarize_a2a_parts(parts: Sequence[Part] | None) -> str | None:
    text = extract_text_from_a2a_parts(parts)
    if text:
        return text[:80]

    if not parts:
        return None

    filenames: list[str] = []
    for part in parts:
        if not _is_file_part(part):
            continue
        name = _normalize_string(part.filename)
        if name:
            filenames.append(name)
        else:
            filenames.append("file")

    if not filenames:
        return None
    if len(filenames) == 1:
        return filenames[0]
    return ", ".join(filenames[:3])[:80]


def map_a2a_parts_to_opencode_parts(parts: Sequence[Part] | None) -> list[OpencodeInputPart]:
    if not parts:
        return []

    mapped: list[OpencodeInputPart] = []
    for index, part in enumerate(parts):
        if part.HasField("text") and part.text:
            mapped.append({"type": "text", "text": part.text})
            continue

        if _is_file_part(part):
            mapped.append(_map_file_part(part, index=index))
            continue

        if part.HasField("data"):
            raise UnsupportedA2AInputError(
                "request.parts["
                f"{index}"
                "] structured data is not supported; use text, raw, or url parts."
            )

        raise UnsupportedA2AInputError(
            f"request.parts[{index}] is not supported; only text, raw, or url parts are accepted."
        )

    return mapped


def _map_file_part(part: Part, *, index: int) -> OpencodeFileInputPart:
    url = _normalize_string(part.url) if part.HasField("url") else None
    if part.HasField("raw") and part.raw:
        mime = _normalize_string(part.media_type) or "application/octet-stream"
        name = _normalize_string(part.filename)
        mapped: OpencodeFileInputPart = {
            "type": "file",
            "url": f"data:{mime};base64,{base64.b64encode(part.raw).decode('ascii')}",
            "mime": mime,
        }
        if name:
            mapped["filename"] = name
        return mapped
    if url:
        mime = _normalize_string(part.media_type) or "application/octet-stream"
        name = _normalize_string(part.filename)
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


def _is_file_part(part: Part) -> bool:
    if part.HasField("raw") and part.raw:
        return True
    return part.HasField("url") and _normalize_string(part.url) is not None


def _normalize_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized else None

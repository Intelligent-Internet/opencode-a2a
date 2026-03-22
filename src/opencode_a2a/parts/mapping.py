from __future__ import annotations

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


class OpencodeToolResultPart(TypedDict, total=False):
    type: Literal["tool"]
    tool: str
    call_id: str
    output: str
    error: str


OpencodeInputPart = OpencodeTextInputPart | OpencodeFileInputPart | OpencodeToolResultPart


def extract_text_from_a2a_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""

    texts: list[str] = []
    for part in parts:
        root = _unwrap_part_root(part)
        if getattr(root, "kind", None) != "text":
            continue
        text = getattr(root, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts).strip()


def summarize_a2a_parts(parts: Any) -> str | None:
    text = extract_text_from_a2a_parts(parts)
    if text:
        return text[:80]

    if not isinstance(parts, list):
        return None

    filenames: list[str] = []
    for part in parts:
        root = _unwrap_part_root(part)
        if getattr(root, "kind", None) != "file":
            continue
        file_value = getattr(root, "file", None)
        name = getattr(file_value, "name", None)
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
    if not isinstance(parts, list):
        return []

    mapped: list[OpencodeInputPart] = []
    for index, part in enumerate(parts):
        root = _unwrap_part_root(part)
        kind = getattr(root, "kind", None)

        if kind == "text":
            text = getattr(root, "text", None)
            if isinstance(text, str):
                mapped.append({"type": "text", "text": text})
            continue

        if kind == "file":
            mapped.append(_map_file_part(root, index=index))
            continue

        if kind == "data":
            raise UnsupportedA2AInputError(
                f"request.parts[{index}] DataPart input is not supported; use TextPart or FilePart."
            )

        raise UnsupportedA2AInputError(
            f"request.parts[{index}] is not supported; only TextPart and FilePart are accepted."
        )

    return mapped


def _map_file_part(part: Any, *, index: int) -> OpencodeFileInputPart:
    file_value = getattr(part, "file", None)
    if file_value is None:
        raise UnsupportedA2AInputError(
            f"request.parts[{index}] FilePart is missing the file payload."
        )

    mime = (
        _normalize_string(
            getattr(file_value, "mime_type", None) or getattr(file_value, "mimeType", None)
        )
        or "application/octet-stream"
    )
    name = _normalize_string(getattr(file_value, "name", None))

    bytes_value = _normalize_string(getattr(file_value, "bytes", None))
    if bytes_value:
        mapped: OpencodeFileInputPart = {
            "type": "file",
            "url": f"data:{mime};base64,{bytes_value}",
            "mime": mime,
        }
        if name:
            mapped["filename"] = name
        return mapped

    uri = _normalize_string(getattr(file_value, "uri", None))
    if uri:
        mapped = {
            "type": "file",
            "url": uri,
            "mime": mime,
        }
        if name:
            mapped["filename"] = name
        return mapped

    raise UnsupportedA2AInputError(
        f"request.parts[{index}] FilePart must contain either bytes or uri."
    )


def _unwrap_part_root(part: Any) -> Any:
    root = getattr(part, "root", None)
    if root is not None:
        return root
    return part


def _normalize_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized else None

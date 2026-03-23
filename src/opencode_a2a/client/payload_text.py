"""Pure helpers for extracting human-readable text from A2A payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a.types import Message, TextPart


def extract_text(payload: Any) -> str | None:
    def extract_from_iterable(items: Any) -> str | None:
        if not isinstance(items, (list, tuple)):
            return None
        for item in items:
            extracted = extract_text(item)
            if extracted:
                return extracted
        return None

    def extract_from_parts(parts: Any) -> str | None:
        if not isinstance(parts, (list, tuple)):
            return None
        collected: list[str] = []
        for part in parts:
            text_part = None
            if isinstance(part, TextPart):
                text_part = part
            else:
                root = getattr(part, "root", None)
                if isinstance(root, TextPart):
                    text_part = root
                elif isinstance(part, Mapping):
                    text_value = part.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        collected.append(text_value)
                        continue
                    mapped_root = part.get("root")
                    if isinstance(mapped_root, TextPart):
                        text_part = mapped_root
                    elif isinstance(part.get("role"), str):
                        nested = extract_text(part)
                        if nested:
                            collected.append(nested)
                            continue
            if text_part and getattr(text_part, "text", None):
                collected.append(text_part.text)
        if collected:
            return "\n".join(collected)
        return None

    def extract_from_mapping(payload_map: Mapping[str, Any]) -> str | None:
        for key in (
            "content",
            "message",
            "messages",
            "result",
            "status",
            "text",
            "parts",
            "artifact",
            "artifacts",
            "history",
            "events",
            "root",
        ):
            if key not in payload_map:
                continue
            value = payload_map[key]
            if value in (None, ""):
                continue
            if key == "text" and isinstance(value, (str, int, float, bool)):
                text_value = str(value).strip()
                if text_value:
                    return text_value
            if key == "parts":
                parts_text = extract_from_parts(value)
                if parts_text:
                    return parts_text
            if key == "artifact":
                artifact_text = extract_text(value)
                if artifact_text:
                    return artifact_text
            if isinstance(value, (list, tuple)) and key in (
                "messages",
                "artifacts",
                "history",
                "events",
            ):
                iterable_text = extract_from_iterable(value)
                if iterable_text:
                    return iterable_text
            nested_text = extract_text(value)
            if nested_text:
                return nested_text
        return None

    if isinstance(payload, (list, tuple)):
        return extract_from_iterable(payload)

    if isinstance(payload, Message):
        return extract_from_parts(payload.parts)

    if isinstance(payload, str):
        return payload.strip() or None

    status_payload = getattr(payload, "status", None)
    if status_payload is not None:
        text = extract_text(status_payload)
        if text:
            return text

    message_payload = getattr(payload, "message", None)
    if message_payload is not None:
        text = extract_text(message_payload)
        if text:
            return text

    artifact_payload = getattr(payload, "artifact", None)
    if artifact_payload is not None:
        text = extract_text(artifact_payload)
        if text:
            return text

    result_payload = getattr(payload, "result", None)
    if result_payload is not None:
        text = extract_text(result_payload)
        if text:
            return text

    history = getattr(payload, "history", None)
    if isinstance(history, (list, tuple)) and history:
        for item in reversed(history):
            text = extract_text(item)
            if text:
                return text

    artifacts = getattr(payload, "artifacts", None)
    if isinstance(artifacts, (list, tuple)):
        for artifact in artifacts:
            artifact_parts = getattr(artifact, "parts", None)
            if isinstance(artifact_parts, (list, tuple)):
                text = extract_from_parts(artifact_parts)
                if text:
                    return text

    text = extract_from_parts(getattr(payload, "parts", None))
    if text:
        return text

    event_text = extract_from_iterable(getattr(payload, "events", None))
    if event_text:
        return event_text

    if isinstance(payload, Mapping):
        mapped_text = extract_from_mapping(payload)
        if mapped_text:
            return mapped_text

    mapping_payload = None
    if hasattr(payload, "model_dump") and callable(payload.model_dump):
        payload_dict = payload.model_dump()
        if isinstance(payload_dict, Mapping):
            mapping_payload = payload_dict
    elif hasattr(payload, "dict") and callable(payload.dict):
        payload_dict = payload.dict()
        if isinstance(payload_dict, Mapping):
            mapping_payload = payload_dict
    elif isinstance(getattr(payload, "__dict__", None), Mapping):
        mapping_payload = dict(payload.__dict__)

    if mapping_payload is not None:
        mapped_text = extract_from_mapping(mapping_payload)
        if mapped_text:
            return mapped_text

    return None


__all__ = ["extract_text"]

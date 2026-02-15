from __future__ import annotations

from typing import Any


def extract_text_from_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            part_text = part.get("text")
            if isinstance(part_text, str):
                texts.append(part_text)
    return "".join(texts).strip()

from __future__ import annotations

from typing import Any


def extract_text_from_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    snapshots: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            part_text = part.get("text")
            if isinstance(part_text, str):
                texts.append(part_text)
            continue
        if part_type in {"snapshot", "step-start", "step-finish"}:
            snapshot = part.get("snapshot")
            if isinstance(snapshot, str) and snapshot.strip():
                snapshots.append(snapshot)
    if texts:
        return "".join(texts).strip()
    if snapshots:
        return snapshots[-1].strip()
    return ""

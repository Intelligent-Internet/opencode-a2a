from __future__ import annotations

import logging
import re
from threading import Lock
from typing import Any

logger = logging.getLogger("opencode_a2a.execution.executor")
_METRIC_NAME_PATTERN = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_registry_lock = Lock()
_registry: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


def _normalized_labels(labels: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    normalized: list[tuple[str, str]] = []
    for key, value in sorted(labels.items()):
        if not _LABEL_NAME_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid metric label name: {key!r}")
        normalized.append((key, str(value).lower() if isinstance(value, bool) else str(value)))
    return tuple(normalized)


def _record_metric(name: str, value: float, labels: tuple[tuple[str, str], ...]) -> None:
    if not _METRIC_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid metric name: {name!r}")
    key = (name, labels)
    with _registry_lock:
        if name.endswith("_total") or name.endswith("_active"):
            _registry[key] = _registry.get(key, 0.0) + value
        else:
            _registry[key] = value


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_prometheus_metrics() -> str:
    """Render the process-local metric registry in Prometheus text format."""
    with _registry_lock:
        samples = sorted(_registry.items())

    lines: list[str] = []
    declared: set[str] = set()
    for (name, labels), value in samples:
        if name not in declared:
            metric_type = "counter" if name.endswith("_total") else "gauge"
            lines.extend(
                (f"# HELP {name} opencode-a2a runtime metric.", f"# TYPE {name} {metric_type}")
            )
            declared.add(name)
        label_text = ""
        if labels:
            escaped = [f'{key}="{_escape_label_value(value)}"' for key, value in labels]
            label_text = "{" + ",".join(escaped) + "}"
        lines.append(f"{name}{label_text} {value:g}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset_metrics() -> None:
    """Clear process-local metrics; intended for isolated tests."""
    with _registry_lock:
        _registry.clear()


def emit_metric(
    name: str,
    value: float = 1.0,
    **labels: str | int | float | bool,
) -> None:
    normalized_labels = _normalized_labels(labels)
    _record_metric(name, value, normalized_labels)
    if labels:
        labels_text = ",".join(
            f"{key}={str(label).lower() if isinstance(label, bool) else label}"
            for key, label in sorted(labels.items())
        )
        logger.debug("metric=%s value=%s labels=%s", name, value, labels_text)
        return
    logger.debug("metric=%s value=%s", name, value)

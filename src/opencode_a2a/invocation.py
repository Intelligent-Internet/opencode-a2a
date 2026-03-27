from __future__ import annotations

import inspect
from typing import Any


def _resolve_signature_target(target):  # noqa: ANN001
    side_effect = getattr(target, "side_effect", None)
    if callable(side_effect):
        return side_effect
    return target


def call_with_supported_kwargs(target, /, *args: Any, **kwargs: Any):  # noqa: ANN001
    signature_target = _resolve_signature_target(target)
    try:
        signature = inspect.signature(signature_target)
    except (TypeError, ValueError):
        return target(*args, **kwargs)

    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return target(*args, **kwargs)

    supported_kwargs = {
        name: value for name, value in kwargs.items() if name in signature.parameters
    }
    return target(*args, **supported_kwargs)

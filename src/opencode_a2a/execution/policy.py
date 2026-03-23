from __future__ import annotations

import os
from pathlib import Path


class PolicyEnforcer:
    def __init__(self, *, client) -> None:
        self._client = client

    def resolve_directory(self, requested: str | None) -> str | None:
        base_dir_str = self._client.directory or os.getcwd()
        base_path = Path(base_dir_str).resolve()

        if requested is not None and not isinstance(requested, str):
            raise ValueError("Directory must be a string path")

        requested = requested.strip() if requested else requested
        if not requested:
            return str(base_path)

        def _resolve_requested(path: str) -> Path:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = base_path / candidate
            return candidate.resolve()

        if not self._client.settings.a2a_allow_directory_override:
            requested_path = _resolve_requested(requested)
            if requested_path == base_path:
                return str(base_path)
            raise ValueError("Directory override is disabled by service configuration")

        requested_path = _resolve_requested(requested)
        try:
            requested_path.relative_to(base_path)
        except ValueError as err:
            raise ValueError(
                f"Directory {requested} is outside the allowed workspace {base_path}"
            ) from err
        return str(requested_path)

    def resolve_directory_for_control(self, requested: str | None) -> str | None:
        return self.resolve_directory(requested)


from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SandboxPolicy:
    workspace_root: Path
    allow_directory_override: bool
    sandbox_mode: str
    filesystem_scope: str
    writable_roots: tuple[Path, ...]
    write_access_scope: str

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        workspace_root: str | None = None,
    ) -> SandboxPolicy:
        base_path = Path(workspace_root or settings.opencode_workspace_root or os.getcwd())
        writable_roots = tuple(
            Path(root).resolve()
            for root in settings.a2a_sandbox_writable_roots
            if isinstance(root, str) and root.strip()
        )
        return cls(
            workspace_root=base_path.resolve(),
            allow_directory_override=settings.a2a_allow_directory_override,
            sandbox_mode=settings.a2a_sandbox_mode,
            filesystem_scope=settings.a2a_sandbox_filesystem_scope,
            writable_roots=writable_roots,
            write_access_scope=settings.a2a_write_access_scope,
        )

    def resolve_directory(
        self,
        requested: str | None,
        *,
        default_directory: str | None = None,
    ) -> str | None:
        base_path = Path(default_directory).resolve() if default_directory else self.workspace_root

        if requested is not None and not isinstance(requested, str):
            raise ValueError("Directory must be a string path")

        requested = requested.strip() if requested else requested
        if not requested:
            return str(base_path)

        requested_path = Path(requested)
        if not requested_path.is_absolute():
            requested_path = base_path / requested_path
        requested_path = requested_path.resolve()

        if not self.allow_directory_override:
            if requested_path == base_path:
                return str(base_path)
            raise ValueError("Directory override is disabled by service configuration")

        try:
            requested_path.relative_to(base_path)
        except ValueError as err:
            raise ValueError(
                f"Directory {requested} is outside the allowed workspace {base_path}"
            ) from err
        return str(requested_path)

    def is_session_shell_enabled(
        self,
        *,
        enabled_by_config: bool,
    ) -> bool:
        if not enabled_by_config:
            return False
        if self.sandbox_mode == "read-only":
            return False
        if self.write_access_scope == "none":
            return False
        return True

    def validate_configuration(self) -> None:
        if self.write_access_scope == "none" and self.writable_roots:
            raise ValueError(
                "Declared writable roots are incompatible with A2A_WRITE_ACCESS_SCOPE=none"
            )
        if self.write_access_scope == "workspace_only" or self.filesystem_scope == "workspace_only":
            outside_workspace = [
                str(root)
                for root in self.writable_roots
                if not _is_within_workspace(root, workspace_root=self.workspace_root)
            ]
            if outside_workspace:
                joined = ", ".join(outside_workspace)
                raise ValueError(
                    "Declared writable roots must stay within the workspace root when "
                    "the configured scope is workspace_only: "
                    f"{joined}"
                )


def _is_within_workspace(path: Path, *, workspace_root: Path) -> bool:
    try:
        path.relative_to(workspace_root)
    except ValueError:
        return False
    return True

from __future__ import annotations

from typing import Any


class WorkspaceControlClientMixin:
    workspace_control_calls: list[dict[str, Any]]
    provider_catalog_payload: dict[str, Any]

    async def list_provider_catalog(
        self,
        *,
        directory: str | None = None,
        workspace_id: str | None = None,
    ):
        self.workspace_control_calls.append(
            {
                "method": "provider_catalog",
                "directory": directory,
                "workspace_id": workspace_id,
            }
        )
        return self.provider_catalog_payload

    async def list_projects(self):
        self.workspace_control_calls.append({"method": "list_projects"})
        return [
            {
                "id": "proj-1",
                "name": "Alpha",
                "vcs": "git",
                "canonical": "/workspace/alpha",
                "directory": "/workspace/alpha",
                "icon": {"url": "https://internal.local/icon.png"},
                "apiKey": "sk-secret",  # pragma: allowlist secret
            }
        ]

    async def get_current_project(self):
        self.workspace_control_calls.append({"method": "get_current_project"})
        return {
            "id": "proj-1",
            "name": "Alpha",
            "vcs": "git",
            "canonical": "/workspace/alpha",
            "directory": "/workspace/alpha",
            "apiKey": "sk-secret",  # pragma: allowlist secret
        }

    async def list_workspaces(self):
        self.workspace_control_calls.append({"method": "list_workspaces"})
        return [
            {
                "id": "wrk-1",
                "type": "git",
                "name": "Alpha workspace",
                "branch": "main",
                "directory": "/workspace/alpha",
            }
        ]

    async def create_workspace(self, request: dict[str, Any]):
        self.workspace_control_calls.append({"method": "create_workspace", "request": request})
        return {
            "id": "wrk-2",
            "type": "git",
            "name": "Created workspace",
            "branch": "main",
            "directory": "/tmp/created-workspace",
            **request,
        }

    async def remove_workspace(self, workspace_id: str):
        self.workspace_control_calls.append(
            {"method": "remove_workspace", "workspace_id": workspace_id}
        )
        return {
            "id": workspace_id,
            "type": "git",
            "name": "Removed workspace",
            "branch": "main",
            "directory": None,
        }

    async def list_worktrees(self):
        self.workspace_control_calls.append({"method": "list_worktrees"})
        return [
            {
                "name": "alpha",
                "branch": "opencode/alpha",
                "directory": "/tmp/worktrees/alpha",
            }
        ]

    async def create_worktree(self, request: dict[str, Any]):
        self.workspace_control_calls.append({"method": "create_worktree", "request": request})
        return {
            "name": request.get("name") or "feature-branch",
            "branch": "opencode/feature-branch",
            "directory": "/tmp/worktrees/feature-branch",
        }

    async def remove_worktree(self, request: dict[str, Any]) -> bool:
        self.workspace_control_calls.append({"method": "remove_worktree", "request": request})
        return True

    async def reset_worktree(self, request: dict[str, Any]) -> bool:
        self.workspace_control_calls.append({"method": "reset_worktree", "request": request})
        return True

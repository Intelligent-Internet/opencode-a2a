from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings

PROFILE_ID = "opencode-a2a-single-tenant-coding-v1"
DEPLOYMENT_PROFILE = "single_tenant_shared_workspace"
SESSION_SHELL_TOGGLE = "A2A_ENABLE_SESSION_SHELL"
DIRECTORY_OVERRIDE_METADATA_FIELD = "metadata.opencode.directory"


@dataclass(frozen=True)
class DeploymentProfile:
    profile_id: str = PROFILE_ID
    deployment_profile: str = DEPLOYMENT_PROFILE
    shared_workspace_across_consumers: bool = True
    tenant_isolation: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "deployment_profile": self.deployment_profile,
            "shared_workspace_across_consumers": self.shared_workspace_across_consumers,
            "tenant_isolation": self.tenant_isolation,
        }


@dataclass(frozen=True)
class RuntimeFeatures:
    directory_override_enabled: bool
    session_shell_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory_override": {
                "enabled": self.directory_override_enabled,
                "metadata_field": DIRECTORY_OVERRIDE_METADATA_FIELD,
                "policy": "server-validated metadata override within the configured boundary",
            },
            "session_shell": {
                "enabled": self.session_shell_enabled,
                "availability": "enabled" if self.session_shell_enabled else "disabled",
                "toggle": SESSION_SHELL_TOGGLE,
            },
        }


@dataclass(frozen=True)
class RuntimeContext:
    project: str | None = None
    workspace_root: str | None = None
    agent: str | None = None
    variant: str | None = None

    def to_dict(self) -> dict[str, str]:
        context: dict[str, str] = {}
        if self.project:
            context["project"] = self.project
        if self.workspace_root:
            context["workspace_root"] = self.workspace_root
        if self.agent:
            context["agent"] = self.agent
        if self.variant:
            context["variant"] = self.variant
        return context


@dataclass(frozen=True)
class RuntimeProfile:
    deployment: DeploymentProfile
    runtime_features: RuntimeFeatures
    runtime_context: RuntimeContext

    @property
    def profile_id(self) -> str:
        return self.deployment.profile_id

    def to_deployment_context(self) -> dict[str, str | bool]:
        deployment_context: dict[str, str | bool] = {
            "allow_directory_override": self.runtime_features.directory_override_enabled,
            "deployment_profile": self.deployment.deployment_profile,
            "shared_workspace_across_consumers": self.deployment.shared_workspace_across_consumers,
            "tenant_isolation": self.deployment.tenant_isolation,
            "session_shell_enabled": self.runtime_features.session_shell_enabled,
        }
        deployment_context.update(self.runtime_context.to_dict())
        return deployment_context

    def to_public_dict(self, *, protocol_version: str | None = None) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "profile_id": self.profile_id,
            "deployment": self.deployment.to_dict(),
            "runtime_features": self.runtime_features.to_dict(),
        }
        if protocol_version:
            profile["protocol_version"] = protocol_version
        runtime_context = self.runtime_context.to_dict()
        if runtime_context:
            profile["runtime_context"] = runtime_context
        return profile


def build_runtime_profile(settings: Settings) -> RuntimeProfile:
    return RuntimeProfile(
        deployment=DeploymentProfile(),
        runtime_features=RuntimeFeatures(
            directory_override_enabled=settings.a2a_allow_directory_override,
            session_shell_enabled=settings.a2a_enable_session_shell,
        ),
        runtime_context=RuntimeContext(
            project=settings.a2a_project,
            workspace_root=settings.opencode_workspace_root,
            agent=settings.opencode_agent,
            variant=settings.opencode_variant,
        ),
    )


def build_contract_profile_metadata(
    runtime_profile: RuntimeProfile,
    *,
    protocol_version: str | None = None,
) -> dict[str, Any]:
    deployment = runtime_profile.deployment
    return {
        "profile": runtime_profile.to_public_dict(protocol_version=protocol_version),
        "deployment_context": runtime_profile.to_deployment_context(),
        "shared_workspace_across_consumers": deployment.shared_workspace_across_consumers,
        "tenant_isolation": deployment.tenant_isolation,
    }

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings

PROFILE_ID = "opencode-a2a-single-tenant-coding-v1"
DEPLOYMENT_ID = "single_tenant_shared_workspace"
SESSION_SHELL_TOGGLE = "A2A_ENABLE_SESSION_SHELL"
DIRECTORY_OVERRIDE_METADATA_FIELD = "metadata.opencode.directory"


@dataclass(frozen=True)
class DeploymentProfile:
    id: str = DEPLOYMENT_ID
    single_tenant: bool = True
    shared_workspace_across_consumers: bool = True
    tenant_isolation: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "single_tenant": self.single_tenant,
            "shared_workspace_across_consumers": self.shared_workspace_across_consumers,
            "tenant_isolation": self.tenant_isolation,
        }


@dataclass(frozen=True)
class DirectoryBindingProfile:
    allow_override: bool
    scope: str
    metadata_field: str = DIRECTORY_OVERRIDE_METADATA_FIELD

    def as_dict(self) -> dict[str, Any]:
        return {
            "allow_override": self.allow_override,
            "scope": self.scope,
            "metadata_field": self.metadata_field,
        }


@dataclass(frozen=True)
class SessionShellProfile:
    enabled: bool
    availability: str
    toggle: str = SESSION_SHELL_TOGGLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "availability": self.availability,
            "toggle": self.toggle,
        }


@dataclass(frozen=True)
class ServiceFeaturesProfile:
    streaming: dict[str, Any]
    health_endpoint: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "streaming": dict(self.streaming),
            "health_endpoint": dict(self.health_endpoint),
        }


@dataclass(frozen=True)
class RuntimeContext:
    project: str | None = None
    workspace_root: str | None = None
    agent: str | None = None
    variant: str | None = None

    def as_dict(self) -> dict[str, str]:
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
    profile_id: str
    deployment: DeploymentProfile
    directory_binding: DirectoryBindingProfile
    session_shell: SessionShellProfile
    service_features: ServiceFeaturesProfile
    runtime_context: RuntimeContext

    @property
    def session_shell_enabled(self) -> bool:
        return self.session_shell.enabled

    def runtime_features_dict(self) -> dict[str, Any]:
        return {
            "directory_binding": self.directory_binding.as_dict(),
            "session_shell": self.session_shell.as_dict(),
            "service_features": self.service_features.as_dict(),
        }

    def summary_dict(self, *, protocol_version: str | None = None) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "profile_id": self.profile_id,
            "deployment": self.deployment.as_dict(),
            "runtime_features": self.runtime_features_dict(),
        }
        runtime_context = self.runtime_context.as_dict()
        if runtime_context:
            profile["runtime_context"] = runtime_context
        if protocol_version:
            profile["protocol_version"] = protocol_version
        return profile

    def health_payload(
        self,
        *,
        service: str,
        version: str,
        protocol_version: str,
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": service,
            "version": version,
            "profile": self.summary_dict(protocol_version=protocol_version),
        }


def build_runtime_profile(settings: Settings) -> RuntimeProfile:
    directory_scope = (
        "workspace_root_or_descendant"
        if settings.a2a_allow_directory_override
        else "workspace_root_only"
    )
    return RuntimeProfile(
        profile_id=PROFILE_ID,
        deployment=DeploymentProfile(),
        directory_binding=DirectoryBindingProfile(
            allow_override=settings.a2a_allow_directory_override,
            scope=directory_scope,
        ),
        session_shell=SessionShellProfile(
            enabled=settings.a2a_enable_session_shell,
            availability="enabled" if settings.a2a_enable_session_shell else "disabled",
        ),
        service_features=ServiceFeaturesProfile(
            streaming={"enabled": True, "availability": "always"},
            health_endpoint={"enabled": True, "availability": "always"},
        ),
        runtime_context=RuntimeContext(
            project=settings.a2a_project,
            workspace_root=settings.opencode_workspace_root,
            agent=settings.opencode_agent,
            variant=settings.opencode_variant,
        ),
    )

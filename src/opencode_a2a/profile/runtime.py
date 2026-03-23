from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..sandbox_policy import SandboxPolicy

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
class SandboxProfile:
    mode: str
    filesystem_scope: str
    writable_roots: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": self.mode,
            "filesystem_scope": self.filesystem_scope,
        }
        if self.writable_roots:
            payload["writable_roots"] = list(self.writable_roots)
        return payload


@dataclass(frozen=True)
class NetworkProfile:
    access: str
    allowed_domains: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"access": self.access}
        if self.allowed_domains:
            payload["allowed_domains"] = list(self.allowed_domains)
        return payload


@dataclass(frozen=True)
class ApprovalProfile:
    policy: str
    escalation_behavior: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "escalation_behavior": self.escalation_behavior,
        }


@dataclass(frozen=True)
class WriteAccessProfile:
    scope: str
    outside_workspace: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "outside_workspace": self.outside_workspace,
        }


@dataclass(frozen=True)
class ExecutionEnvironmentProfile:
    sandbox: SandboxProfile
    network: NetworkProfile
    approval: ApprovalProfile
    write_access: WriteAccessProfile

    def as_dict(self) -> dict[str, Any]:
        return {
            "sandbox": self.sandbox.as_dict(),
            "network": self.network.as_dict(),
            "approval": self.approval.as_dict(),
            "write_access": self.write_access.as_dict(),
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
    execution_environment: ExecutionEnvironmentProfile
    service_features: ServiceFeaturesProfile
    runtime_context: RuntimeContext

    @property
    def session_shell_enabled(self) -> bool:
        return self.session_shell.enabled

    def runtime_features_dict(self) -> dict[str, Any]:
        return {
            "directory_binding": self.directory_binding.as_dict(),
            "session_shell": self.session_shell.as_dict(),
            "execution_environment": self.execution_environment.as_dict(),
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
    sandbox_policy = SandboxPolicy.from_settings(settings)
    shell_enabled = sandbox_policy.is_session_shell_enabled(
        enabled_by_config=settings.a2a_enable_session_shell,
    )
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
            enabled=shell_enabled,
            availability="enabled" if shell_enabled else "disabled",
        ),
        execution_environment=ExecutionEnvironmentProfile(
            sandbox=SandboxProfile(
                mode=settings.a2a_sandbox_mode,
                filesystem_scope=settings.a2a_sandbox_filesystem_scope,
                writable_roots=settings.a2a_sandbox_writable_roots,
            ),
            network=NetworkProfile(
                access=settings.a2a_network_access,
                allowed_domains=settings.a2a_network_allowed_domains,
            ),
            approval=ApprovalProfile(
                policy=settings.a2a_approval_policy,
                escalation_behavior=settings.a2a_approval_escalation_behavior,
            ),
            write_access=WriteAccessProfile(
                scope=settings.a2a_write_access_scope,
                outside_workspace=settings.a2a_write_access_outside_workspace,
            ),
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

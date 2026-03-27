from opencode_a2a.profile.runtime import build_runtime_profile
from tests.support.helpers import make_settings


def test_profile_runtime_splits_deployment_runtime_features_and_health_payload() -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_allow_directory_override=False,
        a2a_enable_session_shell=False,
        a2a_sandbox_mode="workspace-write",
        a2a_sandbox_filesystem_scope="workspace_and_declared_roots",
        a2a_sandbox_writable_roots=("/workspace", "/tmp/opencode"),
        a2a_network_access="restricted",
        a2a_network_allowed_domains=("api.openai.com", "github.com"),
        a2a_approval_policy="on-request",
        a2a_approval_escalation_behavior="manual",
        a2a_write_access_scope="workspace_and_declared_roots",
        a2a_write_access_outside_workspace="disallowed",
        a2a_project="alpha",
        opencode_workspace_root="/workspace",
        opencode_agent="planner",
        opencode_variant="fast",
    )

    profile = build_runtime_profile(settings)

    assert profile.summary_dict(protocol_version=settings.a2a_protocol_version) == {
        "profile_id": "opencode-a2a-single-tenant-coding-v1",
        "protocol_version": "0.3.0",
        "deployment": {
            "id": "single_tenant_shared_workspace",
            "single_tenant": True,
            "shared_workspace_across_consumers": True,
            "tenant_isolation": "none",
        },
        "runtime_features": {
            "directory_binding": {
                "allow_override": False,
                "scope": "workspace_root_only",
                "metadata_field": "metadata.opencode.directory",
            },
            "workspace_binding": {
                "enabled": True,
                "metadata_field": "metadata.opencode.workspace.id",
                "upstream_query_param": "workspace",
                "precedence": "prefer_workspace_else_directory",
            },
            "session_shell": {
                "enabled": False,
                "availability": "disabled",
                "toggle": "A2A_ENABLE_SESSION_SHELL",
            },
            "execution_environment": {
                "sandbox": {
                    "mode": "workspace-write",
                    "filesystem_scope": "workspace_and_declared_roots",
                    "writable_roots": ["/workspace", "/tmp/opencode"],
                },
                "network": {
                    "access": "restricted",
                    "allowed_domains": ["api.openai.com", "github.com"],
                },
                "approval": {
                    "policy": "on-request",
                    "escalation_behavior": "manual",
                },
                "write_access": {
                    "scope": "workspace_and_declared_roots",
                    "outside_workspace": "disallowed",
                },
            },
            "service_features": {
                "streaming": {
                    "enabled": True,
                    "availability": "always",
                },
                "health_endpoint": {
                    "enabled": True,
                    "availability": "always",
                },
            },
        },
        "runtime_context": {
            "project": "alpha",
            "workspace_root": "/workspace",
            "agent": "planner",
            "variant": "fast",
        },
    }
    assert profile.health_payload(
        service="opencode-a2a",
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
    ) == {
        "status": "ok",
        "service": "opencode-a2a",
        "version": settings.a2a_version,
        "profile": profile.summary_dict(protocol_version=settings.a2a_protocol_version),
    }


def test_profile_runtime_uses_conservative_execution_environment_defaults() -> None:
    settings = make_settings(a2a_bearer_token="test-token")

    profile = build_runtime_profile(settings)

    assert profile.runtime_features_dict()["execution_environment"] == {
        "sandbox": {
            "mode": "unknown",
            "filesystem_scope": "unknown",
        },
        "network": {
            "access": "unknown",
        },
        "approval": {
            "policy": "unknown",
            "escalation_behavior": "unknown",
        },
        "write_access": {
            "scope": "unknown",
            "outside_workspace": "unknown",
        },
    }


def test_profile_runtime_disables_shell_when_policy_is_read_only() -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_enable_session_shell=True,
        a2a_sandbox_mode="read-only",
        a2a_write_access_scope="workspace_only",
    )

    profile = build_runtime_profile(settings)

    assert profile.runtime_features_dict()["session_shell"] == {
        "enabled": False,
        "availability": "disabled",
        "toggle": "A2A_ENABLE_SESSION_SHELL",
    }

from opencode_a2a.profile.runtime import build_runtime_profile
from opencode_a2a.protocol_versions import A2A_PROTOCOL_VERSION
from tests.support.settings import make_settings


def test_profile_runtime_splits_deployment_runtime_features_and_health_payload() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
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
        a2a_expose_workspace_root_in_card=True,
    )

    profile = build_runtime_profile(settings)

    assert profile.summary_dict(protocol_version=A2A_PROTOCOL_VERSION) == {
        "profile_id": "opencode-a2a-single-tenant-coding-v1",
        "protocol_version": "1.0",
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
            "workspace_mutations": {
                "enabled": False,
                "availability": "disabled",
                "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
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
                "metrics_endpoint": {
                    "enabled": True,
                    "availability": "enabled",
                    "path": "/metrics",
                    "authentication": "required",
                    "toggle": "A2A_METRICS_ENABLED",
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
        protocol_version=A2A_PROTOCOL_VERSION,
    ) == {
        "status": "ok",
        "service": "opencode-a2a",
        "version": settings.a2a_version,
        "profile": profile.summary_dict(protocol_version=A2A_PROTOCOL_VERSION),
    }


def test_profile_runtime_uses_conservative_execution_environment_defaults() -> None:
    settings = make_settings(test_bearer_token="test-token")

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
    assert profile.runtime_features_dict()["workspace_mutations"] == {
        "enabled": False,
        "availability": "disabled",
        "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
    }


def test_profile_runtime_omits_workspace_root_from_runtime_context_by_default() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_project="alpha",
        opencode_workspace_root="/workspace",
    )

    profile = build_runtime_profile(settings)

    assert profile.runtime_context.as_dict() == {"project": "alpha"}


def test_profile_runtime_disables_shell_when_policy_is_read_only() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
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


def test_profile_runtime_disables_workspace_mutations_when_policy_is_read_only() -> None:
    settings = make_settings(
        test_bearer_token="test-token",
        a2a_enable_workspace_mutations=True,
        a2a_sandbox_mode="read-only",
        a2a_write_access_scope="workspace_only",
    )

    profile = build_runtime_profile(settings)

    assert profile.runtime_features_dict()["workspace_mutations"] == {
        "enabled": False,
        "availability": "disabled",
        "toggle": "A2A_ENABLE_WORKSPACE_MUTATIONS",
    }

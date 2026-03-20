from opencode_a2a_server.runtime_profile import build_runtime_profile
from tests.helpers import make_settings


def test_runtime_profile_splits_deployment_runtime_features_and_health_payload() -> None:
    settings = make_settings(
        a2a_bearer_token="test-token",
        a2a_allow_directory_override=False,
        a2a_enable_session_shell=False,
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
            "session_shell": {
                "enabled": False,
                "availability": "disabled",
                "toggle": "A2A_ENABLE_SESSION_SHELL",
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
        service="opencode-a2a-server",
        version=settings.a2a_version,
        protocol_version=settings.a2a_protocol_version,
    ) == {
        "status": "ok",
        "service": "opencode-a2a-server",
        "version": settings.a2a_version,
        "profile": profile.summary_dict(protocol_version=settings.a2a_protocol_version),
    }

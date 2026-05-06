from __future__ import annotations

from base64 import b64encode

from starlette.requests import Request

from opencode_a2a.auth import (
    CAPABILITY_SESSION_SHELL,
    CAPABILITY_WORKSPACE_MUTATION,
    AuthenticatedPrincipal,
    authenticate_static_credential,
    build_static_auth_credentials,
    request_has_capability,
)
from tests.support.settings import make_settings


def _request_with_principal(principal: AuthenticatedPrincipal | None) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
        }
    )
    request.state.authenticated_principal = principal
    return request


def test_build_static_auth_credentials_uses_registry_only() -> None:
    settings = make_settings(
        a2a_static_auth_credentials=(
            {
                "credential_id": "bot-alpha",
                "scheme": "bearer",
                "token": "token-alpha",
                "principal": "automation-alpha",
            },
            {
                "credential_id": "ops-basic",
                "scheme": "basic",
                "username": "ops",
                "password": "ops-pass",  # pragma: allowlist secret
            },
            {
                "credential_id": "disabled",
                "scheme": "bearer",
                "token": "token-disabled",
                "principal": "disabled",
                "enabled": False,
            },
        )
    )

    credentials = build_static_auth_credentials(settings)

    assert len(credentials) == 2
    assert credentials[0].credential_id == "bot-alpha"
    assert credentials[0].principal == "automation-alpha"
    assert credentials[1].credential_id == "ops-basic"
    assert credentials[1].principal == "ops"
    assert credentials[1].capabilities == (
        CAPABILITY_SESSION_SHELL,
        CAPABILITY_WORKSPACE_MUTATION,
    )


def test_authenticate_static_credential_supports_bearer_and_basic() -> None:
    settings = make_settings(
        a2a_static_auth_credentials=(
            {
                "credential_id": "bot-alpha",
                "scheme": "bearer",
                "token": "token-alpha",
                "principal": "automation-alpha",
            },
            {
                "credential_id": "ops-basic",
                "scheme": "basic",
                "username": "ops",
                "password": "ops-pass",  # pragma: allowlist secret
                "capabilities": ["session_shell"],
            },
        )
    )
    credentials = build_static_auth_credentials(settings)

    bearer_principal = authenticate_static_credential(
        credentials=credentials,
        auth_scheme="Bearer",
        auth_value="token-alpha",
    )
    assert bearer_principal is not None
    assert bearer_principal.identity == "automation-alpha"
    assert bearer_principal.credential_id == "bot-alpha"

    basic_principal = authenticate_static_credential(
        credentials=credentials,
        auth_scheme="Basic",
        auth_value=b64encode(b"ops:ops-pass").decode(),
    )
    assert basic_principal is not None
    assert basic_principal.identity == "ops"
    assert basic_principal.capabilities == (CAPABILITY_SESSION_SHELL,)
    assert basic_principal.credential_id == "ops-basic"


def test_request_has_capability_reads_authenticated_principal() -> None:
    request = _request_with_principal(
        AuthenticatedPrincipal(
            identity="ops",
            auth_scheme="basic",
            capabilities=(CAPABILITY_SESSION_SHELL,),
            credential_id="ops-basic",
        )
    )

    assert request_has_capability(request, CAPABILITY_SESSION_SHELL) is True
    assert request_has_capability(request, CAPABILITY_WORKSPACE_MUTATION) is False

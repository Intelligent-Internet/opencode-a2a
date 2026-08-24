from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass

from starlette.requests import Request

from .config import Settings

AUTOMATION_PRINCIPAL = "automation"

CAPABILITY_SESSION_SHELL = "session_shell"
CAPABILITY_WORKSPACE_MUTATION = "workspace_mutation"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    identity: str
    auth_scheme: str
    capabilities: tuple[str, ...] = ()
    credential_id: str | None = None


@dataclass(frozen=True)
class StaticAuthCredential:
    auth_scheme: str
    principal: str
    capabilities: tuple[str, ...]
    token: str | None = None
    username: str | None = None
    password: str | None = None
    credential_id: str | None = None


def default_capabilities_for_scheme(scheme: str) -> tuple[str, ...]:
    if scheme == "basic":
        return (
            CAPABILITY_SESSION_SHELL,
            CAPABILITY_WORKSPACE_MUTATION,
        )
    return ()


def build_static_auth_credentials(settings: Settings) -> tuple[StaticAuthCredential, ...]:
    credentials: list[StaticAuthCredential] = []
    for entry in settings.a2a_static_auth_credentials:
        if not entry.enabled:
            continue
        capabilities = entry.capabilities or default_capabilities_for_scheme(entry.scheme)
        if entry.scheme == "basic":
            principal = entry.username or ""
        else:
            principal = entry.principal or AUTOMATION_PRINCIPAL
        credentials.append(
            StaticAuthCredential(
                auth_scheme=entry.scheme,
                principal=principal,
                capabilities=tuple(capabilities),
                token=entry.token,
                username=entry.username,
                password=entry.password,
                credential_id=entry.credential_id,
            )
        )
    return tuple(credentials)


def authenticate_static_credential(
    *,
    credentials: tuple[StaticAuthCredential, ...],
    auth_scheme: str,
    auth_value: str,
) -> AuthenticatedPrincipal | None:
    normalized_scheme = auth_scheme.lower()
    if normalized_scheme == "bearer":
        for credential in credentials:
            if credential.auth_scheme != "bearer" or credential.token is None:
                continue
            if secrets.compare_digest(auth_value, credential.token):
                return AuthenticatedPrincipal(
                    identity=credential.principal,
                    auth_scheme="bearer",
                    capabilities=credential.capabilities,
                    credential_id=credential.credential_id,
                )
        return None

    if normalized_scheme != "basic":
        return None

    parsed = decode_basic_credentials(auth_value)
    if parsed is None:
        return None
    username, password = parsed
    for credential in credentials:
        if credential.auth_scheme != "basic":
            continue
        if credential.username is None or credential.password is None:
            continue
        username_matches = secrets.compare_digest(username, credential.username)
        password_matches = secrets.compare_digest(password, credential.password)
        if username_matches and password_matches:
            return AuthenticatedPrincipal(
                identity=credential.principal,
                auth_scheme="basic",
                capabilities=credential.capabilities,
                credential_id=credential.credential_id,
            )
    return None


def decode_basic_credentials(value: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    if not username or not password:
        return None
    return username, password


def get_authenticated_principal(request: Request) -> AuthenticatedPrincipal | None:
    principal = getattr(request.state, "authenticated_principal", None)
    if isinstance(principal, AuthenticatedPrincipal):
        return principal
    return None


def request_has_capability(request: Request, capability: str) -> bool:
    principal = get_authenticated_principal(request)
    if principal is None:
        return False
    return capability in principal.capabilities

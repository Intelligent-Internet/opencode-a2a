from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.context import ServerCallContext


class AuthenticatedIdentityUser(User):
    def __init__(self, identity: str) -> None:
        self._identity = identity

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._identity


def normalize_server_call_context(context: ServerCallContext | None) -> ServerCallContext:
    if context is None:
        return ServerCallContext()

    raw_state = _read_context_attribute(context, "state", default=None)
    raw_requested_extensions = _read_context_attribute(
        context,
        "requested_extensions",
        default=None,
    )
    raw_tenant = _read_context_attribute(context, "tenant", default="")
    raw_user = _read_context_attribute(context, "user", default=None)

    state = _normalize_context_state(raw_state)
    requested_extensions = _normalize_requested_extensions(raw_requested_extensions)
    tenant = _normalize_tenant(raw_tenant)
    identity = state.get("identity")
    user = _normalize_user(raw_user, identity=identity if isinstance(identity, str) else None)

    if (
        isinstance(context, ServerCallContext)
        and raw_state is state
        and raw_user is user
        and raw_tenant == tenant
        and raw_requested_extensions == requested_extensions
    ):
        return context

    return ServerCallContext(
        state=state,
        user=user,
        tenant=tenant,
        requested_extensions=requested_extensions,
    )


def _read_context_attribute(context: Any, name: str, *, default: Any) -> Any:
    try:
        return getattr(context, name)
    except AttributeError:
        return default


def _normalize_context_state(raw_state: Any) -> MutableMapping[str, Any]:
    if isinstance(raw_state, MutableMapping):
        return raw_state
    if isinstance(raw_state, Mapping):
        return dict(raw_state)
    return {}


def _normalize_requested_extensions(raw_extensions: Any) -> set[str]:
    if raw_extensions is None:
        return set()
    if isinstance(raw_extensions, set):
        return raw_extensions
    if isinstance(raw_extensions, str):
        return {raw_extensions}
    try:
        return {str(value) for value in raw_extensions}
    except TypeError:
        return set()


def _normalize_tenant(raw_tenant: Any) -> str:
    return raw_tenant if isinstance(raw_tenant, str) else ""


def _normalize_user(raw_user: Any, *, identity: str | None) -> User:
    if identity:
        if not isinstance(raw_user, User) or isinstance(raw_user, UnauthenticatedUser):
            return AuthenticatedIdentityUser(identity)
    if isinstance(raw_user, User):
        return raw_user
    return UnauthenticatedUser()

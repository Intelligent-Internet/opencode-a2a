from __future__ import annotations

from collections.abc import Mapping, MutableMapping

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

    raw_state = getattr(context, "state", None)
    raw_requested_extensions = getattr(context, "requested_extensions", None)
    raw_tenant = getattr(context, "tenant", "")
    raw_user = getattr(context, "user", None)

    if isinstance(raw_state, MutableMapping):
        state = raw_state
    elif isinstance(raw_state, Mapping):
        state = dict(raw_state)
    else:
        state = {}

    if raw_requested_extensions is None:
        requested_extensions: set[str] = set()
    elif isinstance(raw_requested_extensions, set):
        requested_extensions = raw_requested_extensions
    elif isinstance(raw_requested_extensions, str):
        requested_extensions = {raw_requested_extensions}
    else:
        try:
            requested_extensions = {str(value) for value in raw_requested_extensions}
        except TypeError:
            requested_extensions = set()

    tenant = raw_tenant if isinstance(raw_tenant, str) else ""
    identity = state.get("identity")
    normalized_identity = identity if isinstance(identity, str) else None
    if normalized_identity and (
        not isinstance(raw_user, User) or isinstance(raw_user, UnauthenticatedUser)
    ):
        user: User = AuthenticatedIdentityUser(normalized_identity)
    elif isinstance(raw_user, User):
        user = raw_user
    else:
        user = UnauthenticatedUser()

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

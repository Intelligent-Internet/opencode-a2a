"""Authentication helpers for outbound A2A client configuration."""

from __future__ import annotations

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError

BASIC_AUTH_FORMAT_ERROR = (
    "A2A_CLIENT_BASIC_AUTH must be 'username:password' or a base64-encoded "
    "'username:password' value"
)


def validate_basic_auth(value: str) -> None:
    """Validate a raw or pre-encoded Basic Auth credential value."""
    if ":" in value:
        return
    decoded = _decode_basic_auth(value)
    if b":" not in decoded:
        raise ValueError(BASIC_AUTH_FORMAT_ERROR)


def encode_basic_auth(value: str) -> str:
    """Normalize a raw or pre-encoded Basic Auth value to base64 credentials."""
    if ":" in value:
        return b64encode(value.encode()).decode()
    decoded = _decode_basic_auth(value)
    if b":" not in decoded:
        raise ValueError(BASIC_AUTH_FORMAT_ERROR)
    return b64encode(decoded).decode()


def _decode_basic_auth(value: str) -> bytes:
    padded_value = value + ("=" * (-len(value) % 4))
    try:
        return b64decode(padded_value, validate=True)
    except (BinasciiError, ValueError) as exc:
        raise ValueError(BASIC_AUTH_FORMAT_ERROR) from exc

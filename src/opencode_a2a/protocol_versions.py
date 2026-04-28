from __future__ import annotations

import re
from dataclasses import dataclass

_PROTOCOL_VERSION_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.\d+)?$")
A2A_PROTOCOL_VERSION = "1.0"
A2A_SUPPORTED_PROTOCOL_VERSIONS = (A2A_PROTOCOL_VERSION,)


class UnsupportedProtocolVersionError(ValueError):
    def __init__(self, requested_version: str) -> None:
        self.requested_version = requested_version
        self.supported_protocol_versions = A2A_SUPPORTED_PROTOCOL_VERSIONS
        self.default_protocol_version = A2A_PROTOCOL_VERSION
        super().__init__(
            f"Unsupported A2A protocol version {requested_version!r}. "
            f"Supported versions: {A2A_PROTOCOL_VERSION}."
        )


@dataclass(frozen=True)
class NegotiatedProtocolVersion:
    requested_version: str
    negotiated_version: str
    explicit: bool


def normalize_protocol_version(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Protocol version must be a non-empty string.")
    match = _PROTOCOL_VERSION_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError("Protocol version must use Major.Minor or Major.Minor.Patch format.")
    return f"{match.group('major')}.{match.group('minor')}"


def negotiate_protocol_version(
    *,
    header_value: str | None,
    query_value: str | None,
) -> NegotiatedProtocolVersion:
    raw_header = (header_value or "").strip()
    raw_query = (query_value or "").strip()
    explicit = bool(raw_header or raw_query)
    raw_requested = raw_header or raw_query or A2A_PROTOCOL_VERSION

    try:
        normalized_requested = normalize_protocol_version(raw_requested)
    except ValueError as exc:
        raise UnsupportedProtocolVersionError(raw_requested) from exc

    if normalized_requested != A2A_PROTOCOL_VERSION:
        raise UnsupportedProtocolVersionError(normalized_requested)

    return NegotiatedProtocolVersion(
        requested_version=normalized_requested,
        negotiated_version=normalized_requested,
        explicit=explicit,
    )

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from opencode_a2a.parsing import (
    parse_bool_field,
    parse_int_field,
    parse_string_field,
    parse_timestamp_field,
)


def _error_factory(field: str, message: str) -> ValueError:
    return ValueError(f"{field}: {message}")


@pytest.mark.parametrize(
    ("value", "minimum", "expected"),
    [
        pytest.param(None, None, None, id="none"),
        pytest.param(7, None, 7, id="int"),
        pytest.param("12", None, 12, id="string-int"),
        pytest.param("0", 0, 0, id="string-zero"),
    ],
)
def test_parse_int_field_accepts_supported_values(
    value: object,
    minimum: int | None,
    expected: int | None,
) -> None:
    assert (
        parse_int_field(
            value,
            field="limit",
            error_factory=_error_factory,
            minimum=minimum,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("value", "minimum", "message"),
    [
        pytest.param(True, None, "limit must be an integer", id="bool"),
        pytest.param("abc", None, "limit must be an integer", id="non-numeric-string"),
        pytest.param(1.5, None, "limit must be an integer", id="float"),
        pytest.param(-1, 0, "limit must be >= 0", id="below-minimum"),
    ],
)
def test_parse_int_field_rejects_invalid_values(
    value: object,
    minimum: int | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_int_field(
            value,
            field="limit",
            error_factory=_error_factory,
            minimum=minimum,
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("  hello  ", "hello", id="trim"),
        pytest.param("   ", None, id="blank"),
    ],
)
def test_parse_string_field_normalizes_whitespace(value: object, expected: str | None) -> None:
    assert parse_string_field(value, field="cursor", error_factory=_error_factory) == expected


def test_parse_string_field_rejects_non_strings() -> None:
    with pytest.raises(ValueError, match="cursor must be a string"):
        parse_string_field(42, field="cursor", error_factory=_error_factory)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param(True, True, id="bool"),
        pytest.param(" YES ", True, id="string-true"),
        pytest.param("off", False, id="string-false"),
    ],
)
def test_parse_bool_field_accepts_supported_values(value: object, expected: bool | None) -> None:
    assert parse_bool_field(value, field="roots", error_factory=_error_factory) is expected


@pytest.mark.parametrize("value", [1, "maybe", object()])
def test_parse_bool_field_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="roots must be a boolean"):
        parse_bool_field(value, field="roots", error_factory=_error_factory)


def test_parse_timestamp_field_supports_z_suffix() -> None:
    parsed = parse_timestamp_field(
        "2025-01-02T03:04:05Z",
        field="timestamp",
        error_factory=_error_factory,
    )

    assert parsed == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_timestamp_field_promotes_naive_values_to_utc() -> None:
    parsed = parse_timestamp_field(
        "2025-01-02T03:04:05",
        field="timestamp",
        error_factory=_error_factory,
    )

    assert parsed == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.mark.parametrize("value", [123, "not-a-timestamp"])
def test_parse_timestamp_field_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="timestamp must be a valid ISO 8601 timestamp"):
        parse_timestamp_field(value, field="timestamp", error_factory=_error_factory)

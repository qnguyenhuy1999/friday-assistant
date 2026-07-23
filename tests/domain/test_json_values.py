"""JSON-value validation: accepts JSON-compatible values unchanged, rejects
anything that cannot cross a JSON wire contract."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.json_value import ensure_json_value

VALID_VALUES = [
    None,
    True,
    False,
    0,
    -5,
    3.14,
    "text",
    [1, "two", None],
    {"a": 1, "b": {"c": [True, None]}},
]


@pytest.mark.parametrize("value", VALID_VALUES)
def test_valid_json_values_pass_through_unchanged(value: object) -> None:
    assert ensure_json_value(value) == value


def test_nan_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_json_value(math.nan)


def test_infinity_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_json_value(math.inf)


def test_datetime_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_json_value(datetime.now(UTC))


def test_set_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_json_value({1, 2, 3})


def test_tuple_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_json_value((1, 2))


def test_non_string_mapping_key_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_json_value({1: "a"})


def test_nested_invalid_value_is_rejected_with_a_path() -> None:
    with pytest.raises(DomainValidationError, match=r"\$\.a\[0\]"):
        ensure_json_value({"a": [math.nan]})

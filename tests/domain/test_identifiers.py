"""Identifier value objects: canonical-UUID validation, type-distinctness,
and construction helpers."""

from __future__ import annotations

import uuid

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunId, TaskId

VALID_UUID = "11111111-1111-1111-1111-111111111111"


def test_parse_accepts_a_canonical_uuid_string() -> None:
    assert TaskId.parse(VALID_UUID).value == VALID_UUID


def test_new_generates_a_valid_random_identifier() -> None:
    task_id = TaskId.new()
    uuid.UUID(task_id.value)


def test_str_returns_the_raw_uuid_string() -> None:
    assert str(TaskId.parse(VALID_UUID)) == VALID_UUID


def test_empty_string_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        TaskId.parse("")


def test_non_uuid_string_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        TaskId.parse("not-a-uuid")


def test_different_identifier_types_never_compare_equal_for_the_same_uuid() -> None:
    task_id = TaskId.parse(VALID_UUID)
    run_id = RunId.parse(VALID_UUID)
    assert task_id != run_id  # type: ignore[comparison-overlap]


def test_same_identifier_type_and_value_compare_equal() -> None:
    assert TaskId.parse(VALID_UUID) == TaskId.parse(VALID_UUID)


def test_identifier_is_immutable() -> None:
    task_id = TaskId.parse(VALID_UUID)
    with pytest.raises(AttributeError):
        task_id.value = "22222222-2222-2222-2222-222222222222"  # type: ignore[misc]

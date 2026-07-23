"""Failure record: required non-empty code/message, JSON-compatible details."""

from __future__ import annotations

import math

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.failure import Failure, FailureCause


def _failure(**overrides: object) -> Failure:
    defaults: dict[str, object] = {
        "code": "tool.timeout",
        "message": "Tool did not respond in time",
        "retryable": True,
        "cause": FailureCause.TIMEOUT,
    }
    defaults.update(overrides)
    return Failure(**defaults)  # type: ignore[arg-type]


def test_valid_failure_constructs() -> None:
    failure = _failure()
    assert failure.code == "tool.timeout"
    assert failure.cause is FailureCause.TIMEOUT


def test_empty_code_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _failure(code="")


def test_whitespace_only_code_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _failure(code="   ")


def test_empty_message_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _failure(message="")


def test_non_json_details_are_rejected() -> None:
    with pytest.raises(DomainValidationError):
        _failure(details=math.nan)


def test_failure_is_immutable() -> None:
    failure = _failure()
    with pytest.raises(AttributeError):
        failure.code = "changed"  # type: ignore[misc]


def test_all_failure_causes_are_constructible() -> None:
    for cause in FailureCause:
        _failure(cause=cause)

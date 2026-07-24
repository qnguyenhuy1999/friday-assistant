from datetime import timedelta

import pytest

from friday.application.retry_policy import RetryPolicy
from friday.domain.failure import Failure, FailureCause

FAILURE = Failure("runtime", "failed", retryable=True, cause=FailureCause.RUNTIME)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_attempts", 0, "max_attempts"),
        ("base_delay", timedelta(0), "base_delay"),
        ("multiplier", 0.5, "multiplier"),
        ("max_delay", timedelta(seconds=1), "max_delay"),
    ],
)
def test_retry_policy_rejects_invalid_configuration(
    field: str, value: object, message: str
) -> None:
    values: dict[str, object] = {
        "max_attempts": 3,
        "base_delay": timedelta(seconds=2),
        "multiplier": 2.0,
        "max_delay": timedelta(seconds=10),
    }
    values[field] = value
    with pytest.raises(ValueError, match=message):
        RetryPolicy(**values)  # type: ignore[arg-type]


def test_retry_policy_allows_only_retryable_failures_before_attempt_budget() -> None:
    policy = RetryPolicy(3, timedelta(seconds=1), 2, timedelta(seconds=10))
    non_retryable = Failure("runtime", "failed", retryable=False, cause=FailureCause.RUNTIME)

    assert policy.is_retry_allowed(1, FAILURE)
    assert policy.is_retry_allowed(2, FAILURE)
    assert not policy.is_retry_allowed(3, FAILURE)
    assert not policy.is_retry_allowed(1, non_retryable)


def test_retry_policy_computes_exponential_delay_and_clamps() -> None:
    policy = RetryPolicy(5, timedelta(seconds=2), 3, timedelta(seconds=10))

    assert policy.compute_delay(2) == timedelta(seconds=2)
    assert policy.compute_delay(3) == timedelta(seconds=6)
    assert policy.compute_delay(4) == timedelta(seconds=10)

"""Pure retry scheduling policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from friday.domain.failure import Failure


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_delay: timedelta
    multiplier: float
    max_delay: timedelta

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be greater than zero")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be at least base_delay")

    def is_retry_allowed(self, attempt_number: int, failure: Failure) -> bool:
        """Return whether a failure may create another attempt.

        ``max_attempts`` includes the original attempt: ``max_attempts=3``
        allows attempt 1 to retry into attempt 2 and attempt 2 to retry into
        attempt 3, but attempt 3 may not retry again because ``3 < 3`` is
        false.
        """
        return failure.retryable and attempt_number < self.max_attempts

    def compute_delay(self, next_attempt_number: int) -> timedelta:
        """Return deterministic exponential backoff for a new retry run.

        ``next_attempt_number`` is the number of the new retry Run. Attempt 2
        has exactly ``base_delay`` because its exponent is zero. No jitter is
        applied.
        """
        exponent = next_attempt_number - 2
        delay = self.base_delay * (self.multiplier**exponent)
        return min(self.max_delay, delay)

"""Secret-safe audit record for one outbound dispatch boundary crossing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import DeliveryAttemptId, DeliveryId
from friday.domain.time import ensure_utc

MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH = 128
_FAILURE_CODE = re.compile(r"^[a-z0-9_]+$")


class DeliveryAttemptOutcome(StrEnum):
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass(slots=True)
class DeliveryAttempt:
    """Immutable dispatch identity with a one-way, secret-free outcome."""

    id: DeliveryAttemptId
    delivery_id: DeliveryId
    claim_generation: int
    started_at: datetime
    finished_at: datetime | None
    outcome: DeliveryAttemptOutcome
    failure_code: str | None = None
    _identity_frozen: bool = field(init=False, default=False, repr=False)

    _IMMUTABLE_FIELDS = frozenset({"id", "delivery_id", "claim_generation", "started_at"})

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_identity_frozen", False) and name in self._IMMUTABLE_FIELDS:
            raise AttributeError(f"DeliveryAttempt.{name} is immutable")
        object.__setattr__(self, name, value)

    @classmethod
    def begin(
        cls,
        *,
        id: DeliveryAttemptId,
        delivery_id: DeliveryId,
        claim_generation: int,
        started_at: datetime,
    ) -> DeliveryAttempt:
        return cls(
            id, delivery_id, claim_generation, started_at, None, DeliveryAttemptOutcome.IN_PROGRESS
        )

    def __post_init__(self) -> None:
        if self.claim_generation <= 0:
            raise DomainValidationError(
                "DeliveryAttempt.claim_generation must be greater than zero"
            )
        self.started_at = ensure_utc(self.started_at)
        self.finished_at = ensure_utc(self.finished_at) if self.finished_at else None
        if self.outcome is DeliveryAttemptOutcome.IN_PROGRESS:
            if self.finished_at is not None or self.failure_code is not None:
                raise DomainValidationError(
                    "DeliveryAttempt in_progress must have no finished_at or failure_code"
                )
        elif self.finished_at is None:
            raise DomainValidationError("DeliveryAttempt terminal outcome requires finished_at")
        self.failure_code = self._validate_failure_code(self.failure_code)
        self._identity_frozen = True

    @staticmethod
    def _validate_failure_code(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _FAILURE_CODE.fullmatch(value):
            raise DomainValidationError(
                "DeliveryAttempt.failure_code must be a stable lowercase code"
            )
        if len(value) > MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH:
            raise DomainValidationError("DeliveryAttempt.failure_code is too long")
        return value

    def complete(
        self,
        *,
        outcome: DeliveryAttemptOutcome,
        finished_at: datetime,
        failure_code: str | None = None,
    ) -> None:
        if self.outcome is not DeliveryAttemptOutcome.IN_PROGRESS:
            raise InvalidStateTransition("DeliveryAttempt", self.outcome.value, outcome.value)
        if outcome is DeliveryAttemptOutcome.IN_PROGRESS:
            raise DomainValidationError("DeliveryAttempt cannot complete as in_progress")
        self.outcome = outcome
        self.finished_at = ensure_utc(finished_at)
        self.failure_code = self._validate_failure_code(failure_code)

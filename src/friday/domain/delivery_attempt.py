"""Secret-safe audit record for one outbound dispatch boundary crossing.

A `DeliveryAttempt` answers exactly one question, durably and forever: did
Friday cross the external side-effect boundary for this delivery generation,
and how did that crossing end? It is an audit record, so it is one-way — an
attempt begins IN_PROGRESS and reaches exactly one terminal outcome, once.

Two guard rails make that true rather than merely intended:

* Identity fields (`id`, `delivery_id`, `claim_generation`, `started_at`) are
  frozen once constructed. Nothing may retarget an existing audit row.
* Lifecycle fields (`outcome`, `finished_at`, `failure_code`) reject ordinary
  assignment too. `complete()` is the only mutator, and it validates every
  input *before* touching a single field, so a rejected completion leaves the
  aggregate field-for-field unchanged.

Nothing about the destination is stored here: no endpoint, body, secret, or
provider/exception text. `failure_code` is a Friday-owned stable lowercase
code, bounded in length, and never free-form transport output.
"""

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


#: Terminal outcomes that must carry a stable Friday failure code.
_REQUIRES_FAILURE_CODE = frozenset(
    {DeliveryAttemptOutcome.FAILED, DeliveryAttemptOutcome.AMBIGUOUS}
)


def validate_delivery_attempt_failure_code(value: str | None) -> str | None:
    """Return `value` if it is a stable, bounded, lowercase failure code.

    Rejects free-form provider or exception text: an audit ledger must never
    become a place where transport output (and therefore possibly a secret)
    can be smuggled into durable storage.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not _FAILURE_CODE.fullmatch(value):
        raise DomainValidationError("DeliveryAttempt.failure_code must be a stable lowercase code")
    if len(value) > MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH:
        raise DomainValidationError(
            "DeliveryAttempt.failure_code must be at most "
            f"{MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH} characters"
        )
    return value


def validate_delivery_attempt_shape(
    *,
    outcome: DeliveryAttemptOutcome,
    started_at: datetime,
    finished_at: datetime | None,
    failure_code: str | None,
) -> str | None:
    """Validate one attempt's lifecycle shape and return its failure code.

    The single source of truth for which `(outcome, finished_at,
    failure_code)` triples are legal. Construction, `complete()`, and the
    repository's direct SQL writes all route through here, so no write path
    can persist a shape the domain would reject. Raises before returning on
    any violation, and never mutates anything.
    """
    if outcome is DeliveryAttemptOutcome.IN_PROGRESS:
        if finished_at is not None:
            raise DomainValidationError("DeliveryAttempt in_progress must have no finished_at")
        if failure_code is not None:
            raise DomainValidationError("DeliveryAttempt in_progress must have no failure_code")
        return None
    if finished_at is None:
        raise DomainValidationError("DeliveryAttempt terminal outcome requires finished_at")
    if finished_at < started_at:
        raise DomainValidationError("DeliveryAttempt.finished_at must not precede started_at")
    if outcome is DeliveryAttemptOutcome.DELIVERED and failure_code is not None:
        raise DomainValidationError("DeliveryAttempt delivered must have no failure_code")
    if outcome in _REQUIRES_FAILURE_CODE and failure_code is None:
        raise DomainValidationError(
            f"DeliveryAttempt {outcome.value} requires a stable failure_code"
        )
    return validate_delivery_attempt_failure_code(failure_code)


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
    _frozen: bool = field(init=False, default=False, repr=False)

    #: Dispatch identity: which delivery, which claim generation, when. An
    #: audit row that could be retargeted would not be an audit row.
    _IDENTITY_FIELDS = frozenset({"id", "delivery_id", "claim_generation", "started_at"})

    #: One-way lifecycle state. `complete()` is the only legal writer and it
    #: bypasses this guard with `object.__setattr__` after validating.
    _LIFECYCLE_FIELDS = frozenset({"outcome", "finished_at", "failure_code"})

    def __setattr__(self, name: str, value: object) -> None:
        if not getattr(self, "_frozen", False):
            object.__setattr__(self, name, value)
            return
        if name in self._IDENTITY_FIELDS:
            raise AttributeError(f"DeliveryAttempt.{name} is immutable")
        if name in self._LIFECYCLE_FIELDS:
            raise AttributeError(
                f"DeliveryAttempt.{name} changes only through complete(); "
                "direct assignment is not a lifecycle transition"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if self.claim_generation <= 0:
            raise DomainValidationError(
                "DeliveryAttempt.claim_generation must be greater than zero"
            )
        started_at = ensure_utc(self.started_at)
        finished_at = ensure_utc(self.finished_at) if self.finished_at is not None else None
        failure_code = validate_delivery_attempt_shape(
            outcome=self.outcome,
            started_at=started_at,
            finished_at=finished_at,
            failure_code=self.failure_code,
        )
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "finished_at", finished_at)
        object.__setattr__(self, "failure_code", failure_code)
        object.__setattr__(self, "_frozen", True)

    @classmethod
    def begin(
        cls,
        *,
        id: DeliveryAttemptId,
        delivery_id: DeliveryId,
        claim_generation: int,
        started_at: datetime,
    ) -> DeliveryAttempt:
        """Open a new IN_PROGRESS attempt for one boundary crossing."""
        return cls(
            id, delivery_id, claim_generation, started_at, None, DeliveryAttemptOutcome.IN_PROGRESS
        )

    def complete(
        self,
        *,
        outcome: DeliveryAttemptOutcome,
        finished_at: datetime,
        failure_code: str | None = None,
    ) -> None:
        """Close this attempt into exactly one terminal outcome, once.

        Exception-safe by construction: every input is validated into local
        variables first, and only a fully valid set of values is written. A
        rejected completion therefore leaves the attempt field-for-field
        unchanged and still IN_PROGRESS.
        """
        if self.outcome is not DeliveryAttemptOutcome.IN_PROGRESS:
            raise InvalidStateTransition("DeliveryAttempt", self.outcome.value, outcome.value)
        if outcome is DeliveryAttemptOutcome.IN_PROGRESS:
            raise DomainValidationError("DeliveryAttempt cannot complete as in_progress")
        finished = ensure_utc(finished_at)
        code = validate_delivery_attempt_shape(
            outcome=outcome,
            started_at=self.started_at,
            finished_at=finished,
            failure_code=failure_code,
        )
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "finished_at", finished)
        object.__setattr__(self, "failure_code", code)

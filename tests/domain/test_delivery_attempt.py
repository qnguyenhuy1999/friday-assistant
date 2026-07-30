"""DeliveryAttempt is a one-way, secret-free, immutable audit record."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from friday.domain.delivery_attempt import (
    MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH,
    DeliveryAttempt,
    DeliveryAttemptOutcome,
    validate_delivery_attempt_failure_code,
    validate_delivery_attempt_shape,
)
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import DeliveryAttemptId, DeliveryId

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)
LATER = T0 + timedelta(seconds=5)

#: Every field that carries state, so a "nothing changed" assertion cannot
#: silently stop covering a field someone adds later.
_STATE_FIELDS = (
    "id",
    "delivery_id",
    "claim_generation",
    "started_at",
    "finished_at",
    "outcome",
    "failure_code",
)


def _begun(started_at: datetime = T0, *, claim_generation: int = 1) -> DeliveryAttempt:
    return DeliveryAttempt.begin(
        id=DeliveryAttemptId.new(),
        delivery_id=DeliveryId.new(),
        claim_generation=claim_generation,
        started_at=started_at,
    )


def _snapshot(attempt: DeliveryAttempt) -> dict[str, object]:
    return {name: getattr(attempt, name) for name in _STATE_FIELDS}


def test_begin_opens_exactly_one_in_progress_attempt() -> None:
    attempt = _begun(claim_generation=3)

    assert attempt.outcome is DeliveryAttemptOutcome.IN_PROGRESS
    assert attempt.claim_generation == 3
    assert attempt.started_at == T0
    assert attempt.finished_at is None
    assert attempt.failure_code is None


def test_begin_normalizes_started_at_to_utc_and_rejects_naive() -> None:
    from datetime import timezone

    offset = datetime(2026, 1, 2, 5, tzinfo=timezone(timedelta(hours=2)))
    assert _begun(offset).started_at == T0

    with pytest.raises(DomainValidationError, match="timezone-aware"):
        _begun(datetime(2026, 1, 2, 3))  # noqa: DTZ001 - deliberately naive


@pytest.mark.parametrize("claim_generation", [0, -1])
def test_begin_requires_a_real_claim_generation(claim_generation: int) -> None:
    """Generation 0 means "never claimed", so it can never have dispatched."""
    with pytest.raises(DomainValidationError, match="claim_generation"):
        _begun(claim_generation=claim_generation)


@pytest.mark.parametrize("field", ["id", "delivery_id", "claim_generation", "started_at"])
def test_identity_is_immutable(field: str) -> None:
    attempt = _begun()
    before = _snapshot(attempt)

    with pytest.raises(AttributeError, match="immutable"):
        setattr(attempt, field, getattr(_begun(LATER, claim_generation=2), field))

    assert _snapshot(attempt) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", DeliveryAttemptOutcome.DELIVERED),
        ("finished_at", LATER),
        ("failure_code", "webhook_http_5xx"),
    ],
)
def test_lifecycle_state_rejects_ordinary_assignment(field: str, value: object) -> None:
    """Only complete() may move lifecycle state; assignment is not a transition."""
    attempt = _begun()
    before = _snapshot(attempt)

    with pytest.raises(AttributeError, match="complete"):
        setattr(attempt, field, value)

    assert _snapshot(attempt) == before


def test_lifecycle_state_stays_locked_after_completion() -> None:
    attempt = _begun()
    attempt.complete(outcome=DeliveryAttemptOutcome.DELIVERED, finished_at=LATER)
    before = _snapshot(attempt)

    with pytest.raises(AttributeError, match="complete"):
        attempt.outcome = DeliveryAttemptOutcome.FAILED
    with pytest.raises(AttributeError, match="complete"):
        attempt.failure_code = "tampered"

    assert _snapshot(attempt) == before


def test_complete_records_a_delivered_outcome() -> None:
    attempt = _begun()
    attempt.complete(outcome=DeliveryAttemptOutcome.DELIVERED, finished_at=LATER)

    assert attempt.outcome is DeliveryAttemptOutcome.DELIVERED
    assert attempt.finished_at == LATER
    assert attempt.failure_code is None


@pytest.mark.parametrize(
    "outcome", [DeliveryAttemptOutcome.FAILED, DeliveryAttemptOutcome.AMBIGUOUS]
)
def test_complete_records_a_coded_terminal_outcome(outcome: DeliveryAttemptOutcome) -> None:
    attempt = _begun()
    attempt.complete(outcome=outcome, finished_at=LATER, failure_code="webhook_timeout")

    assert attempt.outcome is outcome
    assert attempt.finished_at == LATER
    assert attempt.failure_code == "webhook_timeout"


def test_complete_allows_a_zero_duration_attempt() -> None:
    """finished_at == started_at is legal; only going backwards is not."""
    attempt = _begun()
    attempt.complete(outcome=DeliveryAttemptOutcome.DELIVERED, finished_at=T0)
    assert attempt.finished_at == T0


def test_double_completion_is_rejected() -> None:
    attempt = _begun()
    attempt.complete(outcome=DeliveryAttemptOutcome.DELIVERED, finished_at=LATER)
    before = _snapshot(attempt)

    with pytest.raises(InvalidStateTransition):
        attempt.complete(
            outcome=DeliveryAttemptOutcome.FAILED,
            finished_at=LATER + timedelta(seconds=1),
            failure_code="webhook_http_5xx",
        )

    assert _snapshot(attempt) == before


def test_complete_cannot_close_as_in_progress() -> None:
    attempt = _begun()
    before = _snapshot(attempt)

    with pytest.raises(DomainValidationError, match="in_progress"):
        attempt.complete(outcome=DeliveryAttemptOutcome.IN_PROGRESS, finished_at=LATER)

    assert _snapshot(attempt) == before


def test_completion_before_started_at_is_rejected_and_changes_nothing() -> None:
    attempt = _begun(LATER)
    before = _snapshot(attempt)

    with pytest.raises(DomainValidationError, match="must not precede started_at"):
        attempt.complete(outcome=DeliveryAttemptOutcome.DELIVERED, finished_at=T0)

    assert _snapshot(attempt) == before
    assert attempt.outcome is DeliveryAttemptOutcome.IN_PROGRESS


def test_delivered_with_a_failure_code_is_rejected_and_changes_nothing() -> None:
    """A delivered attempt asserting a failure would be self-contradictory."""
    attempt = _begun()
    before = _snapshot(attempt)

    with pytest.raises(DomainValidationError, match="delivered must have no failure_code"):
        attempt.complete(
            outcome=DeliveryAttemptOutcome.DELIVERED,
            finished_at=LATER,
            failure_code="webhook_http_5xx",
        )

    assert _snapshot(attempt) == before


@pytest.mark.parametrize(
    "outcome", [DeliveryAttemptOutcome.FAILED, DeliveryAttemptOutcome.AMBIGUOUS]
)
def test_coded_outcome_without_a_code_is_rejected_and_changes_nothing(
    outcome: DeliveryAttemptOutcome,
) -> None:
    attempt = _begun()
    before = _snapshot(attempt)

    with pytest.raises(DomainValidationError, match="requires a stable failure_code"):
        attempt.complete(outcome=outcome, finished_at=LATER)

    assert _snapshot(attempt) == before


@pytest.mark.parametrize(
    "failure_code",
    [
        "Webhook_Timeout",
        "webhook timeout",
        "webhook-timeout",
        "webhook.timeout",
        "",
        "x" * (MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH + 1),
        "connection refused to https://hook.test/secret",
        "Traceback (most recent call last)",
    ],
    ids=[
        "uppercase",
        "whitespace",
        "dash",
        "dot",
        "empty",
        "too_long",
        "endpoint_text",
        "exception_text",
    ],
)
def test_invalid_failure_code_is_rejected_and_leaves_the_attempt_unchanged(
    failure_code: str,
) -> None:
    """Free-form provider/exception text must never reach the ledger."""
    attempt = _begun()
    before = _snapshot(attempt)

    with pytest.raises(DomainValidationError):
        attempt.complete(
            outcome=DeliveryAttemptOutcome.FAILED, finished_at=LATER, failure_code=failure_code
        )

    assert _snapshot(attempt) == before
    assert attempt.outcome is DeliveryAttemptOutcome.IN_PROGRESS


def test_failure_code_at_the_maximum_length_is_accepted() -> None:
    code = "x" * MAX_DELIVERY_ATTEMPT_FAILURE_CODE_LENGTH
    assert validate_delivery_attempt_failure_code(code) == code


def test_failure_code_validator_passes_none_through() -> None:
    assert validate_delivery_attempt_failure_code(None) is None


def test_reconstruction_accepts_a_legitimate_terminal_row() -> None:
    """The mapper must be able to load history the ledger legally wrote."""
    attempt = DeliveryAttempt(
        DeliveryAttemptId.new(),
        DeliveryId.new(),
        2,
        T0,
        LATER,
        DeliveryAttemptOutcome.FAILED,
        "webhook_http_5xx",
    )

    assert attempt.outcome is DeliveryAttemptOutcome.FAILED
    assert (attempt.started_at, attempt.finished_at) == (T0, LATER)
    # ...and reconstruction is not a mutation bypass.
    with pytest.raises(AttributeError):
        attempt.outcome = DeliveryAttemptOutcome.DELIVERED


@pytest.mark.parametrize(
    ("outcome", "finished_at", "failure_code", "match"),
    [
        (DeliveryAttemptOutcome.IN_PROGRESS, LATER, None, "no finished_at"),
        (DeliveryAttemptOutcome.IN_PROGRESS, None, "code", "no failure_code"),
        (DeliveryAttemptOutcome.DELIVERED, None, None, "requires finished_at"),
        (DeliveryAttemptOutcome.FAILED, None, "code", "requires finished_at"),
        (DeliveryAttemptOutcome.DELIVERED, LATER, "code", "no failure_code"),
        (DeliveryAttemptOutcome.AMBIGUOUS, LATER, None, "requires a stable failure_code"),
    ],
    ids=[
        "in_progress_finished",
        "in_progress_coded",
        "delivered_unfinished",
        "failed_unfinished",
        "delivered_coded",
        "ambiguous_uncoded",
    ],
)
def test_reconstruction_rejects_an_impossible_persisted_shape(
    outcome: DeliveryAttemptOutcome,
    finished_at: datetime | None,
    failure_code: str | None,
    match: str,
) -> None:
    """A corrupt row fails loudly on read rather than being silently trusted."""
    with pytest.raises(DomainValidationError, match=match):
        DeliveryAttempt(
            DeliveryAttemptId.new(), DeliveryId.new(), 1, T0, finished_at, outcome, failure_code
        )


def test_shape_validator_is_pure_and_returns_the_code() -> None:
    assert (
        validate_delivery_attempt_shape(
            outcome=DeliveryAttemptOutcome.FAILED,
            started_at=T0,
            finished_at=LATER,
            failure_code="webhook_http_4xx",
        )
        == "webhook_http_4xx"
    )
    assert (
        validate_delivery_attempt_shape(
            outcome=DeliveryAttemptOutcome.IN_PROGRESS,
            started_at=T0,
            finished_at=None,
            failure_code=None,
        )
        is None
    )


def test_attempts_are_distinct_by_identity_not_by_shape() -> None:
    """Two attempts for the same boundary are still different audit rows."""
    first = _begun()
    second = replace(first, id=DeliveryAttemptId.new())
    assert first != second
    assert first.delivery_id == second.delivery_id

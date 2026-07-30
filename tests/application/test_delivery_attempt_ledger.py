"""The durable attempt ledger tracks every dispatch boundary crossing.

These are the Step-5 capability proofs at the application seam: the boundary
and its ledger row are created together under one exact claim, terminal
outcomes close the matching attempt, a stale worker can do neither, an invalid
outcome writes nothing, and a delivery that never crossed the boundary has no
attempt at all.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from friday.application.delivery_lifecycle import (
    LEASE_EXPIRED_AFTER_DISPATCH,
    BeginDeliveryAttempt,
    ClaimNextDelivery,
    DeliveryClaim,
    PersistDeliveryOutcome,
    RecoverExpiredDeliveryClaims,
)
from friday.application.errors import ClaimLost
from friday.application.ports import (
    MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT,
    validate_delivery_attempt_history_limit,
)
from friday.application.retry_policy import RetryPolicy
from friday.domain.delivery_attempt import DeliveryAttempt, DeliveryAttemptOutcome
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import DeliveryAttemptId, DeliveryId, RunId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

FINGERPRINT = "a" * 64
LEASE = timedelta(minutes=1)
RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=timedelta(seconds=30),
    multiplier=2.0,
    max_delay=timedelta(minutes=5),
)


def _delivery(available_at: datetime = T0) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id="personal.notifications",
        route_fingerprint=FINGERPRINT,
        body="hello",
        available_at=available_at,
        created_at=T0,
    )


def _claim(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> tuple[OutboundDelivery, DeliveryClaim]:
    delivery = _delivery()
    fake_uow.deliveries.add(delivery)
    claim = ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    ).execute()
    assert claim is not None
    return delivery, claim


def _attempt(fake_uow: FakeUnitOfWork, claim: DeliveryClaim) -> DeliveryAttempt:
    attempt = fake_uow.delivery_attempts.get_for_generation(
        claim.delivery_id, claim.claim_generation
    )
    assert attempt is not None
    return attempt


def test_begin_creates_the_boundary_marker_and_exactly_one_in_progress_attempt(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    clock.fixed_now = T0 + timedelta(seconds=5)

    boundary = BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.dispatch_started_at == boundary
    attempt = _attempt(fake_uow, claim)
    assert attempt.outcome is DeliveryAttemptOutcome.IN_PROGRESS
    assert attempt.started_at == boundary
    assert attempt.claim_generation == claim.claim_generation
    assert attempt.finished_at is None and attempt.failure_code is None
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10) == [attempt]


def test_begin_is_a_committed_transaction_with_no_network_work_inside(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """The unit of work must be committed and closed before dispatch happens."""
    _, claim = _claim(fake_uow, uow_factory, clock)
    commits_before = fake_uow.commit_count

    BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    assert fake_uow.commit_count == commits_before + 1
    assert fake_uow.closed is True


def test_begin_crosses_the_boundary_exactly_once(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """A second begin is fenced out, leaving exactly one attempt."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    begin = BeginDeliveryAttempt(uow_factory, clock)
    boundary = begin.execute(claim)

    clock.fixed_now = T0 + timedelta(seconds=10)
    with pytest.raises(ClaimLost):
        begin.execute(claim)

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.dispatch_started_at == boundary
    assert len(fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10)) == 1


@pytest.mark.parametrize(
    "stale",
    [
        lambda claim: replace(claim, worker_id="worker-b"),
        lambda claim: replace(claim, claim_token="other-token"),
        lambda claim: replace(claim, claim_generation=claim.claim_generation + 1),
    ],
    ids=["wrong_owner", "wrong_token", "stale_generation"],
)
def test_a_stale_claim_can_neither_cross_the_boundary_nor_open_an_attempt(
    fake_uow: FakeUnitOfWork,
    uow_factory: CountingUnitOfWorkFactory,
    clock: FakeClock,
    stale: object,
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)

    with pytest.raises(ClaimLost):
        BeginDeliveryAttempt(uow_factory, clock).execute(stale(claim))  # type: ignore[operator]

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.dispatch_started_at is None
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10) == []


def test_an_expired_claim_cannot_open_an_attempt(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    clock.fixed_now = claim.lease_expires_at

    with pytest.raises(ClaimLost):
        BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.dispatch_started_at is None
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10) == []


def test_no_repository_primitive_can_open_an_attempt_without_a_crossed_boundary(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """There is no generic add: begin_for_claim is the only creation path."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    attempts = fake_uow.delivery_attempts

    assert not hasattr(attempts, "add")
    assert not hasattr(attempts, "save")

    # Claim is valid, but the delivery has not crossed the boundary yet.
    assert not attempts.begin_for_claim(
        DeliveryAttemptId.new(),
        claim.delivery_id,
        claim.worker_id,
        claim.claim_token,
        claim.claim_generation,
        T0,
        T0,
    )
    # An unclaimed delivery cannot have an attempt either.
    unclaimed = _delivery()
    fake_uow.deliveries.add(unclaimed)
    assert not attempts.begin_for_claim(
        DeliveryAttemptId.new(), unclaimed.id, "worker-a", "token", 1, T0, T0
    )
    assert attempts.list_for_delivery(delivery.id, 10) == []
    assert attempts.list_for_delivery(unclaimed.id, 10) == []


def test_begin_for_claim_rejects_a_boundary_it_did_not_cross(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """started_at must be the delivery's own durable boundary timestamp."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    boundary = BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    assert not fake_uow.delivery_attempts.begin_for_claim(
        DeliveryAttemptId.new(),
        claim.delivery_id,
        claim.worker_id,
        claim.claim_token,
        claim.claim_generation,
        boundary + timedelta(seconds=1),
        boundary,
    )
    assert len(fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10)) == 1


def test_delivered_outcome_closes_the_matching_attempt(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    clock.fixed_now = T0 + timedelta(seconds=2)

    PersistDeliveryOutcome(uow_factory, clock).deliver(claim, provider_message_id="p-1")

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.DELIVERED
    attempt = _attempt(fake_uow, claim)
    assert attempt.outcome is DeliveryAttemptOutcome.DELIVERED
    assert attempt.finished_at == clock.fixed_now
    assert attempt.failure_code is None


def test_definite_failure_closes_the_attempt_with_the_delivery_failure_code(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    clock.fixed_now = T0 + timedelta(seconds=2)

    PersistDeliveryOutcome(uow_factory, clock).fail(
        claim, failure_code="webhook_http_4xx", failure_message="Webhook delivery failed."
    )

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.FAILED
    attempt = _attempt(fake_uow, claim)
    assert attempt.outcome is DeliveryAttemptOutcome.FAILED
    assert attempt.failure_code == "webhook_http_4xx"


def test_ambiguous_outcome_closes_the_attempt_as_ambiguous(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    clock.fixed_now = T0 + timedelta(seconds=2)

    PersistDeliveryOutcome(uow_factory, clock).mark_ambiguous(
        claim, failure_code="webhook_timeout", failure_message="Outcome unknown."
    )

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    attempt = _attempt(fake_uow, claim)
    assert attempt.outcome is DeliveryAttemptOutcome.AMBIGUOUS
    assert attempt.failure_code == "webhook_timeout"


@pytest.mark.parametrize(
    "stale",
    [
        lambda claim: replace(claim, worker_id="worker-b"),
        lambda claim: replace(claim, claim_token="other-token"),
        lambda claim: replace(claim, claim_generation=claim.claim_generation + 1),
    ],
    ids=["wrong_owner", "wrong_token", "stale_generation"],
)
def test_a_stale_claim_cannot_close_an_attempt(
    fake_uow: FakeUnitOfWork,
    uow_factory: CountingUnitOfWorkFactory,
    clock: FakeClock,
    stale: object,
) -> None:
    _, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    with pytest.raises(ClaimLost):
        PersistDeliveryOutcome(uow_factory, clock).fail(
            stale(claim),  # type: ignore[operator]
            failure_code="webhook_http_4xx",
            failure_message="m",
        )

    assert _attempt(fake_uow, claim).outcome is DeliveryAttemptOutcome.IN_PROGRESS


def test_an_invalid_outcome_produces_zero_durable_mutation(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """Free-form transport text is rejected before delivery or ledger changes."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    before = fake_uow.deliveries.get(delivery.id)
    assert before is not None
    clock.fixed_now = T0 + timedelta(seconds=2)

    with pytest.raises(DomainValidationError):
        PersistDeliveryOutcome(uow_factory, clock).fail(
            claim,
            failure_code="Connection refused to https://hook.test/secret",
            failure_message="m",
        )

    after = fake_uow.deliveries.get(delivery.id)
    assert after is not None
    assert after.status is DeliveryStatus.SENDING
    assert after.failure_code == before.failure_code
    assert after.updated_at == before.updated_at
    assert _attempt(fake_uow, claim).outcome is DeliveryAttemptOutcome.IN_PROGRESS


def test_route_drift_parks_the_delivery_and_creates_no_attempt(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """A pre-dispatch safety stop crossed no boundary, so it has no audit row."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)

    PersistDeliveryOutcome(uow_factory, clock).mark_route_ambiguous(
        claim,
        failure_code="delivery_route_fingerprint_mismatch",
        failure_message="Delivery route authority changed; no message was sent.",
    )

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None
    assert stored.status is DeliveryStatus.AMBIGUOUS
    assert stored.dispatch_started_at is None
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10) == []


def test_a_pre_dispatch_outcome_still_requires_the_boundary_for_delivered(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """Regression: the ledger did not weaken the boundary requirement."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)

    with pytest.raises(InvalidStateTransition):
        PersistDeliveryOutcome(uow_factory, clock).deliver(claim)

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.SENDING
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10) == []


def test_expired_post_dispatch_recovery_closes_the_same_attempt_as_ambiguous(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    boundary = BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    opened = _attempt(fake_uow, claim)

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    assert (
        RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=10).execute(
            fake_uow, recovered_at
        )
        == 1
    )

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    closed = _attempt(fake_uow, claim)
    # The same audit row, closed — not a new one.
    assert closed.id == opened.id
    assert closed.started_at == boundary
    assert closed.outcome is DeliveryAttemptOutcome.AMBIGUOUS
    assert closed.failure_code == LEASE_EXPIRED_AFTER_DISPATCH
    assert closed.finished_at == recovered_at
    assert len(fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10)) == 1


def test_pre_dispatch_recovery_creates_no_attempt_and_keeps_retrying(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """Regression: pre-dispatch RetryPolicy behaviour is untouched by the ledger."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)

    recovered_at = claim.lease_expires_at + timedelta(seconds=1)
    assert (
        RecoverExpiredDeliveryClaims(RETRY_POLICY, candidate_limit=10).execute(
            fake_uow, recovered_at
        )
        == 1
    )

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.QUEUED
    assert stored.available_at == recovered_at + RETRY_POLICY.compute_delay(2)
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10) == []


def test_a_new_generation_opens_its_own_attempt(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """One attempt per boundary crossing, keyed by claim generation."""
    delivery, first = _claim(fake_uow, uow_factory, clock)
    claimer = ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    )

    # Generation 1 expires pre-dispatch, so it never opens an attempt.
    clock.fixed_now = first.lease_expires_at + timedelta(seconds=1)
    assert claimer.execute() is None
    requeued = fake_uow.deliveries.get(delivery.id)
    assert requeued is not None

    clock.fixed_now = requeued.available_at
    second = claimer.execute()
    assert second is not None and second.claim_generation == 2
    BeginDeliveryAttempt(uow_factory, clock).execute(second)

    history = fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10)
    assert [a.claim_generation for a in history] == [2]


def test_a_delivery_can_never_cross_the_boundary_a_second_time(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """One boundary per delivery, so at most one attempt, forever.

    `dispatch_started_at` is one-way and a post-dispatch delivery can never
    return to QUEUED, so a post-dispatch delivery is never reclaimable. This is
    what makes "exactly one external message per delivery" true, and it is why
    the history read is naturally a single row rather than a growing list.
    """
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    clock.fixed_now = claim.lease_expires_at + timedelta(seconds=1)
    claimer = ClaimNextDelivery(
        uow_factory,
        clock,
        RETRY_POLICY,
        worker_id="worker-a",
        lease_duration=LEASE,
        candidate_limit=5,
    )
    # Recovery parks it AMBIGUOUS; nothing is ever claimable again.
    assert claimer.execute() is None
    clock.fixed_now = clock.fixed_now + timedelta(days=365)
    assert claimer.execute() is None

    stored = fake_uow.deliveries.get(delivery.id)
    assert stored is not None and stored.status is DeliveryStatus.AMBIGUOUS
    assert len(fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10)) == 1


def test_history_read_is_bounded_and_ordered_newest_boundary_first(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """Ordering contract: started_at DESC, then id DESC.

    Legal dispatch produces at most one attempt per delivery, so the multi-row
    ordering path is seeded directly here — the same shape migration 0014's
    backfill and the `UNIQUE(delivery_id, claim_generation)` constraint permit.
    The real SQLite repository is held to the identical contract in
    `tests/persistence/test_delivery_attempt_repository.py`.
    """
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    boundary = T0 + timedelta(seconds=30)
    tied = sorted((DeliveryAttemptId.new() for _ in range(2)), key=str, reverse=True)
    for generation, (attempt_id, started_at) in enumerate(
        [(tied[0], boundary), (tied[1], boundary), (DeliveryAttemptId.new(), T0)], start=2
    ):
        fake_uow.delivery_attempt_repo.items[(delivery.id, generation)] = DeliveryAttempt.begin(
            id=attempt_id,
            delivery_id=delivery.id,
            claim_generation=generation,
            started_at=started_at,
        )

    history = fake_uow.delivery_attempts.list_for_delivery(delivery.id, 10)
    assert [(a.started_at, str(a.id)) for a in history] == sorted(
        ((a.started_at, str(a.id)) for a in history), reverse=True
    )
    assert [a.started_at for a in history[:2]] == [boundary, boundary]
    assert str(history[0].id) > str(history[1].id)
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 1) == history[:1]
    assert fake_uow.delivery_attempts.list_for_delivery(delivery.id, 2) == history[:2]


@pytest.mark.parametrize("limit", [0, -1, -1000, MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT + 1], ids=str)
def test_history_rejects_an_out_of_range_limit(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock, limit: int
) -> None:
    """A negative LIMIT is unbounded in SQLite, so it must never get through."""
    delivery, _ = _claim(fake_uow, uow_factory, clock)

    with pytest.raises(ValueError, match="between 1 and"):
        fake_uow.delivery_attempts.list_for_delivery(delivery.id, limit)


@pytest.mark.parametrize("limit", [1, 2, MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT])
def test_history_accepts_the_full_permitted_range(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock, limit: int
) -> None:
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)

    assert len(fake_uow.delivery_attempts.list_for_delivery(delivery.id, limit)) == 1


def test_history_limit_validator_rejects_non_integers() -> None:
    assert validate_delivery_attempt_history_limit(1) == 1
    assert (
        validate_delivery_attempt_history_limit(MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT)
        == MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT
    )
    with pytest.raises(ValueError, match="must be an integer"):
        validate_delivery_attempt_history_limit(True)  # noqa: FBT003 - bool is not a limit
    with pytest.raises(ValueError, match="must be an integer"):
        validate_delivery_attempt_history_limit("10")  # type: ignore[arg-type]


def test_no_endpoint_body_or_secret_is_stored_in_an_attempt(
    fake_uow: FakeUnitOfWork, uow_factory: CountingUnitOfWorkFactory, clock: FakeClock
) -> None:
    """The ledger records that a boundary was crossed, never what was sent."""
    delivery, claim = _claim(fake_uow, uow_factory, clock)
    BeginDeliveryAttempt(uow_factory, clock).execute(claim)
    clock.fixed_now = T0 + timedelta(seconds=2)
    PersistDeliveryOutcome(uow_factory, clock).fail(
        claim, failure_code="webhook_http_5xx", failure_message="Webhook delivery failed."
    )

    attempt = _attempt(fake_uow, claim)
    fields = {f for f in dir(attempt) if not f.startswith("_")}
    assert not fields & {"endpoint", "body", "subject", "secret", "route_id", "payload"}
    serialized = repr(attempt)
    assert delivery.body not in serialized
    assert delivery.route_id not in serialized

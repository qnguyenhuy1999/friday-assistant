"""Fast unit tests for the worker delivery loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from threading import Event

import pytest

from apps.worker.worker_loop import WorkerLoop
from friday.application.retry_policy import RetryPolicy
from friday.application.run_processor import ClaimContext, ProcessingOutcome
from friday.application.worker_coordination import (
    ApplyFailedOutcome,
    ApplySucceededOutcome,
    ApplyWaitingOutcome,
    ClaimNextRun,
    ReleaseRunClaim,
    RenewRunLease,
    RequeueClaimedRun,
)
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run, RunStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

LEASE = timedelta(minutes=1)
FAILURE = Failure("test", "failed", retryable=False, cause=FailureCause.RUNTIME)


@dataclass
class PresetProcessor:
    outcome: ProcessingOutcome
    seen: ClaimContext | None = None
    before_outcome: Callable[[ClaimContext], None] | None = None

    def process(self, context: ClaimContext) -> ProcessingOutcome:
        self.seen = context
        if self.before_outcome is not None:
            self.before_outcome(context)
        return self.outcome


def _run_and_loop(
    outcome: ProcessingOutcome,
) -> tuple[FakeUnitOfWork, Run, WorkerLoop, PresetProcessor]:
    uow = FakeUnitOfWork()
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    uow.runs.add(run)
    uow.work_queue.enqueue(run.id, T0, T0)
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock()
    retry = RetryPolicy(3, timedelta(seconds=1), 2.0, timedelta(seconds=10))
    loop = WorkerLoop(
        claim_next_run=ClaimNextRun(
            factory, clock, worker_id="worker", lease_duration=LEASE, candidate_limit=10
        ),
        renew_lease=RenewRunLease(factory, clock, lease_duration=LEASE),
        requeue_claimed_run=RequeueClaimedRun(factory, clock),
        apply_failed=ApplyFailedOutcome(factory, clock, retry_policy=retry),
        apply_succeeded=ApplySucceededOutcome(factory, clock),
        apply_waiting=ApplyWaitingOutcome(factory, clock),
        recover_expired_leases=RecoverExpiredLeases(factory, clock, batch_size=10),
        expire_due_approvals=ExpireDueApprovals(factory, clock, batch_size=10),
        heartbeat_interval_seconds=0.001,
        maintenance_interval_seconds=60,
        poll_interval_seconds=0.001,
    )
    processor = PresetProcessor(outcome)
    return uow, run, loop, processor


def test_run_once_returns_false_without_processor() -> None:
    _, _, loop, _ = _run_and_loop(ProcessingOutcome.succeeded())
    assert loop.run_once(None) is False


def test_run_once_returns_false_when_nothing_is_due() -> None:
    uow, _, loop, processor = _run_and_loop(ProcessingOutcome.succeeded())
    uow.work_queue_repo.enqueue(
        next(iter(uow.work_queue_repo.items)), T0 + timedelta(seconds=1), T0
    )
    assert loop.run_once(processor) is False


@pytest.mark.parametrize(
    "outcome",
    [
        ProcessingOutcome.succeeded(),
        ProcessingOutcome.failed(FAILURE),
        ProcessingOutcome.waiting_for_approval(),
        ProcessingOutcome.yielded(T0 + timedelta(minutes=5)),
    ],
)
def test_run_once_dispatches_each_outcome(outcome: ProcessingOutcome) -> None:
    uow, run, loop, processor = _run_and_loop(outcome)
    if outcome.kind == "waiting_for_approval":

        def wait_for_approval(_: ClaimContext) -> None:
            run.wait_for_approval(T0, ApprovalRequestId.new())

        processor.before_outcome = wait_for_approval

    assert loop.run_once(processor) is True
    assert processor.seen is not None
    item = uow.work_queue_repo.get(run.id)
    if outcome.kind == "succeeded":
        assert run.status is RunStatus.SUCCEEDED and item is None
    elif outcome.kind == "failed":
        assert run.status is RunStatus.FAILED and item is None
    elif outcome.kind == "waiting_for_approval":
        assert run.status is RunStatus.WAITING_FOR_APPROVAL and item is None
    else:
        assert item is not None and item.available_at == outcome.available_at
        assert item.claimed_by is None


def test_run_once_swallow_claim_lost_during_outcome_application() -> None:
    uow, run, loop, _ = _run_and_loop(ProcessingOutcome.succeeded())
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock()
    processor = PresetProcessor(ProcessingOutcome.succeeded())

    def steal_claim(context: ClaimContext) -> None:
        ReleaseRunClaim(factory, clock).execute(
            context.run_id, context.worker_id, context.claim_token, context.claim_generation
        )
        assert ClaimNextRun(
            factory, clock, worker_id="other", lease_duration=LEASE, candidate_limit=10
        ).execute()

    processor.before_outcome = steal_claim
    loop._claim_next_run = ClaimNextRun(
        factory, clock, worker_id="worker", lease_duration=LEASE, candidate_limit=10
    )
    assert loop.run_once(processor) is True
    assert run.status is RunStatus.RUNNING


def test_run_maintenance_tick_recovers_expired_lease() -> None:
    uow, run, loop, _ = _run_and_loop(ProcessingOutcome.succeeded())
    factory = CountingUnitOfWorkFactory(uow)
    clock = FakeClock()
    claim = ClaimNextRun(
        factory, clock, worker_id="other", lease_duration=LEASE, candidate_limit=10
    ).execute()
    assert claim is not None
    clock.fixed_now = T0 + LEASE
    loop._recover_expired_leases = RecoverExpiredLeases(factory, clock, batch_size=10)
    loop._expire_due_approvals = ExpireDueApprovals(factory, clock, batch_size=10)
    loop.run_maintenance_tick()
    item = uow.work_queue_repo.get(run.id)
    assert item is not None and item.claimed_by is None


def test_serve_forever_stops_when_shutdown_is_already_set() -> None:
    _, _, loop, _ = _run_and_loop(ProcessingOutcome.succeeded())
    shutdown = Event()
    shutdown.set()
    loop.serve_forever(shutdown)

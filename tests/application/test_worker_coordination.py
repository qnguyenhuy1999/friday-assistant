from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.errors import ClaimLost
from friday.application.worker_coordination import (
    ClaimNextRun,
    CompleteRunWorkItem,
    ReleaseRunClaim,
    RenewRunLease,
    RequeueClaimedRun,
    VerifyRunClaim,
)
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run, RunStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

LEASE = timedelta(minutes=1)
FAILURE = Failure("test", "failed", retryable=False, cause=FailureCause.RUNTIME)


def _prepared_run(status: RunStatus = RunStatus.QUEUED) -> tuple[FakeUnitOfWork, Run]:
    uow = FakeUnitOfWork()
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    if status is not RunStatus.QUEUED:
        run.start(T0)
    if status is RunStatus.WAITING_FOR_APPROVAL:
        run.wait_for_approval(T0, ApprovalRequestId.new())
    elif status is RunStatus.SUCCEEDED:
        run.succeed(T0)
    elif status is RunStatus.FAILED:
        run.fail(T0, FAILURE)
    elif status is RunStatus.CANCELLED:
        run.cancel(T0)
    uow.run_repo.add(run)
    uow.work_queue_repo.enqueue(run.id, T0, T0)
    return uow, run


def _claim(
    uow: FakeUnitOfWork, run_id: RunId, *, worker_id: str = "worker-1", token: str = "token-1"
) -> int:
    assert uow.work_queue_repo.try_claim(run_id, worker_id, token, T0, T0 + LEASE)
    item = uow.work_queue_repo.get(run_id)
    assert item is not None
    return item.claim_generation


def test_claim_next_run_returns_none_when_no_due_work_exists() -> None:
    uow, run = _prepared_run()
    uow.work_queue_repo.enqueue(run.id, T0 + timedelta(seconds=1), T0)
    factory = CountingUnitOfWorkFactory(uow)

    assert (
        ClaimNextRun(
            factory, FakeClock(T0), worker_id="worker", lease_duration=LEASE, candidate_limit=5
        ).execute()
        is None
    )
    assert factory.calls == 1


def test_claim_next_run_starts_queued_run_and_returns_fenced_claim() -> None:
    uow, run = _prepared_run()
    result = ClaimNextRun(
        CountingUnitOfWorkFactory(uow),
        FakeClock(T0),
        worker_id="worker",
        lease_duration=LEASE,
        candidate_limit=5,
    ).execute()

    assert result is not None
    assert result.run_id == run.id
    assert result.worker_id == "worker"
    assert result.claim_token
    assert result.claim_generation == 1
    assert result.lease_expires_at == T0 + LEASE
    assert run.status is RunStatus.RUNNING
    assert [event.type for event in uow.event_store.appended] == [RunEventType.RUN_STARTED]


def test_claim_next_run_reclaims_running_run_without_second_started_event() -> None:
    uow, run = _prepared_run(RunStatus.RUNNING)
    result = ClaimNextRun(
        CountingUnitOfWorkFactory(uow),
        FakeClock(T0),
        worker_id="worker",
        lease_duration=LEASE,
        candidate_limit=5,
    ).execute()

    assert result is not None
    assert run.status is RunStatus.RUNNING
    assert uow.event_store.appended == []


def test_claim_next_run_removes_stale_items_then_claims_next_due_run() -> None:
    uow = FakeUnitOfWork()
    missing_id = RunId.new()
    uow.work_queue_repo.enqueue(missing_id, T0, T0)
    for offset, status in enumerate(
        [
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING_FOR_APPROVAL,
        ],
        start=1,
    ):
        _, run = _prepared_run(status)
        uow.run_repo.add(run)
        uow.work_queue_repo.enqueue(run.id, T0, T0 + timedelta(seconds=offset))
    valid = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    uow.run_repo.add(valid)
    uow.work_queue_repo.enqueue(valid.id, T0, T0 + timedelta(seconds=10))

    result = ClaimNextRun(
        CountingUnitOfWorkFactory(uow),
        FakeClock(T0),
        worker_id="worker",
        lease_duration=LEASE,
        candidate_limit=10,
    ).execute()

    assert result is not None and result.run_id == valid.id
    assert missing_id not in uow.work_queue_repo.items
    assert len(uow.work_queue_repo.items) == 1


def test_claim_next_run_respects_candidate_limit() -> None:
    uow = FakeUnitOfWork()
    uow.work_queue_repo.enqueue(RunId.new(), T0, T0)
    valid = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    uow.run_repo.add(valid)
    uow.work_queue_repo.enqueue(valid.id, T0, T0 + timedelta(seconds=1))

    assert (
        ClaimNextRun(
            CountingUnitOfWorkFactory(uow),
            FakeClock(T0),
            worker_id="worker",
            lease_duration=LEASE,
            candidate_limit=1,
        ).execute()
        is None
    )
    assert uow.work_queue_repo.get(valid.id) is not None


@pytest.mark.parametrize(
    "worker_id, token, generation",
    [("other", "token-1", 1), ("worker-1", "other", 1), ("worker-1", "token-1", 2)],
)
def test_renew_run_lease_raises_claim_lost_for_ownership_mismatch(
    worker_id: str, token: str, generation: int
) -> None:
    uow, run = _prepared_run()
    _claim(uow, run.id)

    with pytest.raises(ClaimLost):
        RenewRunLease(CountingUnitOfWorkFactory(uow), FakeClock(T0), lease_duration=LEASE).execute(
            run.id, worker_id, token, generation
        )


def test_renew_run_lease_renews_only_unexpired_matching_claim() -> None:
    uow, run = _prepared_run()
    generation = _claim(uow, run.id)
    renewed_at = T0 + timedelta(seconds=30)

    expires_at = RenewRunLease(
        CountingUnitOfWorkFactory(uow), FakeClock(renewed_at), lease_duration=LEASE
    ).execute(run.id, "worker-1", "token-1", generation)

    assert expires_at == renewed_at + LEASE
    assert uow.work_queue_repo.get(run.id).lease_expires_at == expires_at  # type: ignore[union-attr]
    with pytest.raises(ClaimLost):
        RenewRunLease(
            CountingUnitOfWorkFactory(uow), FakeClock(expires_at), lease_duration=LEASE
        ).execute(run.id, "worker-1", "token-1", generation)


def test_release_and_requeue_clear_claim_fields_and_preserve_generation() -> None:
    uow, run = _prepared_run()
    generation = _claim(uow, run.id)
    ReleaseRunClaim(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
        run.id, "worker-1", "token-1", generation
    )
    released = uow.work_queue_repo.get(run.id)
    assert released is not None
    assert (released.claimed_by, released.claim_token, released.lease_expires_at) == (
        None,
        None,
        None,
    )
    assert released.claim_generation == generation
    with pytest.raises(ClaimLost):
        ReleaseRunClaim(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
            run.id, "worker-1", "bad", generation
        )

    next_generation = _claim(uow, run.id, token="token-2")
    available_at = T0 + timedelta(minutes=5)
    RequeueClaimedRun(CountingUnitOfWorkFactory(uow), FakeClock(T0)).execute(
        run.id, "worker-1", "token-2", next_generation, available_at
    )
    requeued = uow.work_queue_repo.get(run.id)
    assert requeued is not None
    assert requeued.available_at == available_at
    assert (requeued.claimed_by, requeued.claim_token, requeued.lease_expires_at) == (
        None,
        None,
        None,
    )
    with pytest.raises(ClaimLost):
        RequeueClaimedRun(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
            run.id, "worker-1", "bad", next_generation, available_at
        )


def test_complete_run_work_item_removes_only_matching_claim() -> None:
    uow, run = _prepared_run()
    generation = _claim(uow, run.id)
    with pytest.raises(ClaimLost):
        CompleteRunWorkItem(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
            run.id, "worker-1", "bad", generation
        )
    assert uow.work_queue_repo.get(run.id) is not None

    CompleteRunWorkItem(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
        run.id, "worker-1", "token-1", generation
    )
    assert uow.work_queue_repo.get(run.id) is None


def test_verify_run_claim_returns_false_without_raising_for_invalid_or_expired_claim() -> None:
    uow, run = _prepared_run()
    generation = _claim(uow, run.id)
    verify = VerifyRunClaim(CountingUnitOfWorkFactory(uow), FakeClock(T0))
    assert verify.execute(run.id, "worker-1", "token-1", generation)
    assert not verify.execute(run.id, "other", "token-1", generation)
    assert not verify.execute(run.id, "worker-1", "other", generation)
    assert not verify.execute(run.id, "worker-1", "token-1", generation + 1)
    assert not VerifyRunClaim(CountingUnitOfWorkFactory(uow), FakeClock(T0 + LEASE)).execute(
        run.id, "worker-1", "token-1", generation
    )


@pytest.mark.parametrize("operation", ["renew", "release", "requeue", "complete"])
def test_stale_worker_is_fenced_after_newer_claim_generation(operation: str) -> None:
    uow, run = _prepared_run()
    old_generation = _claim(uow, run.id)
    reclaimed_at = T0 + LEASE
    assert uow.work_queue_repo.try_claim(
        run.id, "worker-2", "token-2", reclaimed_at, reclaimed_at + LEASE
    )

    with pytest.raises(ClaimLost):
        if operation == "renew":
            RenewRunLease(
                CountingUnitOfWorkFactory(uow), FakeClock(reclaimed_at), lease_duration=LEASE
            ).execute(run.id, "worker-1", "token-1", old_generation)
        elif operation == "release":
            ReleaseRunClaim(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
                run.id, "worker-1", "token-1", old_generation
            )
        elif operation == "requeue":
            RequeueClaimedRun(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
                run.id, "worker-1", "token-1", old_generation, reclaimed_at
            )
        else:
            CompleteRunWorkItem(CountingUnitOfWorkFactory(uow), FakeClock()).execute(
                run.id, "worker-1", "token-1", old_generation
            )

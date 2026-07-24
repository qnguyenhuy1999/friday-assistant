from __future__ import annotations

from datetime import datetime, timedelta

from friday.application.approval_workflow import RequestApproval
from friday.application.commands import RequestApprovalCommand
from friday.application.results import ApprovalRequestResult
from friday.application.worker_maintenance import ExpireDueApprovals, RecoverExpiredLeases
from friday.domain.approval import ApprovalCategory, ApprovalStatus
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run, RunStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

LEASE = timedelta(minutes=1)
NOW = T0 + LEASE


def _claimed_run(status: RunStatus = RunStatus.RUNNING) -> tuple[FakeUnitOfWork, Run]:
    uow = FakeUnitOfWork()
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    if status is not RunStatus.QUEUED:
        run.start(T0)
    if status is RunStatus.WAITING_FOR_APPROVAL:
        run.wait_for_approval(T0, ApprovalRequestId.new())
    elif status is RunStatus.SUCCEEDED:
        run.succeed(T0)
    elif status is RunStatus.FAILED:
        run.fail(T0, Failure("test", "failed", False, FailureCause.RUNTIME))
    elif status is RunStatus.CANCELLED:
        run.cancel(T0)
    uow.run_repo.add(run)
    uow.work_queue_repo.enqueue(run.id, T0, T0)
    assert uow.work_queue_repo.try_claim(run.id, "worker", "token", T0, T0 + LEASE)
    return uow, run


def test_recover_expired_lease_clears_claim_and_preserves_run_and_generation() -> None:
    uow, run = _claimed_run()
    item = uow.work_queue_repo.get(run.id)
    assert item is not None
    generation = item.claim_generation

    assert (
        RecoverExpiredLeases(
            CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
        ).execute()
        == 1
    )

    recovered = uow.work_queue_repo.get(run.id)
    assert recovered is not None
    assert run.status is RunStatus.RUNNING
    assert recovered.claim_generation == generation
    assert recovered.claimed_by is None
    assert uow.work_queue_repo.try_claim(run.id, "new-worker", "new-token", NOW, NOW + LEASE)


def test_recover_expired_lease_clears_claim_for_queued_run() -> None:
    uow, run = _claimed_run(RunStatus.QUEUED)

    assert (
        RecoverExpiredLeases(
            CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
        ).execute()
        == 1
    )
    assert run.status is RunStatus.QUEUED
    assert uow.work_queue_repo.get(run.id) is not None


def test_recover_expired_lease_removes_items_for_terminal_and_waiting_runs() -> None:
    for status in (
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.WAITING_FOR_APPROVAL,
    ):
        uow, run = _claimed_run(status)
        assert (
            RecoverExpiredLeases(
                CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
            ).execute()
            == 1
        )
        assert uow.work_queue_repo.get(run.id) is None


def test_recover_expired_leases_respects_batch_size_across_ticks() -> None:
    uow = FakeUnitOfWork()
    runs: list[Run] = []
    for _ in range(3):
        run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
        run.start(T0)
        uow.run_repo.add(run)
        uow.work_queue_repo.enqueue(run.id, T0, T0)
        assert uow.work_queue_repo.try_claim(run.id, "worker", "token", T0, T0 + LEASE)
        runs.append(run)
    maintenance = RecoverExpiredLeases(CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=2)

    assert maintenance.execute() == 2
    assert maintenance.execute() == 1
    assert all(uow.work_queue_repo.get(run.id) is not None for run in runs)


def _request_due_approval(
    uow: FakeUnitOfWork, *, expires_at: datetime | None
) -> tuple[Run, ApprovalRequestResult]:
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    run.start(T0)
    uow.run_repo.add(run)
    result = RequestApproval(CountingUnitOfWorkFactory(uow), FakeClock(T0)).execute(
        RequestApprovalCommand(
            run_id=run.id,
            category=ApprovalCategory.OTHER,
            summary="maintenance test",
            reason="test",
            requested_action="do test",
            requested_input=None,
            expires_at=expires_at,
        )
    )
    return run, result


def test_expire_due_approvals_expires_and_reenqueues_waiting_run() -> None:
    uow = FakeUnitOfWork()
    run, result = _request_due_approval(uow, expires_at=NOW)

    expired = ExpireDueApprovals(
        CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
    ).execute()

    assert len(expired) == 1
    assert expired[0].approval_id == result.approval_id
    assert expired[0].status is ApprovalStatus.EXPIRED
    assert run.status is RunStatus.RUNNING
    work_item = uow.work_queue_repo.get(run.id)
    assert work_item is not None
    assert work_item.available_at == NOW


def test_expire_due_approvals_skips_not_due_and_missing_deadline() -> None:
    uow = FakeUnitOfWork()
    not_due_run, not_due = _request_due_approval(uow, expires_at=NOW + LEASE)
    no_deadline_run, no_deadline = _request_due_approval(uow, expires_at=None)

    expired = ExpireDueApprovals(
        CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
    ).execute()

    assert expired == []
    assert uow.approval_repo.items[not_due.approval_id].status is ApprovalStatus.PENDING
    assert uow.approval_repo.items[no_deadline.approval_id].status is ApprovalStatus.PENDING
    assert not_due_run.status is RunStatus.WAITING_FOR_APPROVAL
    assert no_deadline_run.status is RunStatus.WAITING_FOR_APPROVAL


def test_expire_due_approvals_replay_is_empty_without_duplicate_events() -> None:
    uow = FakeUnitOfWork()
    run, result = _request_due_approval(uow, expires_at=NOW)
    maintenance = ExpireDueApprovals(CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10)

    first = maintenance.execute()
    event_count = len(uow.event_store.appended)
    second = maintenance.execute()

    assert first[0].approval_id == result.approval_id
    assert second == []
    assert len(uow.event_store.appended) == event_count
    assert run.status is RunStatus.RUNNING


def test_expire_due_approvals_respects_batch_size() -> None:
    uow = FakeUnitOfWork()
    results = [_request_due_approval(uow, expires_at=NOW)[1] for _ in range(3)]
    maintenance = ExpireDueApprovals(CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=2)

    assert len(maintenance.execute()) == 2
    assert len(maintenance.execute()) == 1
    assert {result.approval_id for result in maintenance.execute()} == set()
    assert all(
        uow.approval_repo.items[result.approval_id].status is ApprovalStatus.EXPIRED
        for result in results
    )

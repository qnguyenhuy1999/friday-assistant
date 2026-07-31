from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest

from friday.application.approval_workflow import RequestApproval
from friday.application.commands import RequestApprovalCommand
from friday.application.results import ApprovalRequestResult
from friday.application.worker_maintenance import (
    ExpireDueApprovals,
    MaterializeScheduledAnswerDeliveries,
    RecoverExpiredLeases,
    ScheduledAnswerContentGate,
)
from friday.domain.approval import ApprovalCategory, ApprovalStatus
from friday.domain.event import RunEvent, RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    ApprovalRequestId,
    RunEventId,
    RunId,
    ScheduleFireDeliveryPlanId,
    ScheduleFireId,
    ScheduleId,
    TaskId,
)
from friday.domain.json_value import JsonValue
from friday.domain.outbound_delivery import DeliverySourceKind
from friday.domain.run import Run, RunStatus
from friday.domain.schedule_fire_delivery_plan import ScheduleFireDeliveryPlan
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


def _ready_plan(
    uow: FakeUnitOfWork,
    run: Run,
    *,
    route_max_body_chars: int = 16000,
    created_at: datetime = T0,
) -> ScheduleFireId:
    fire_id = ScheduleFireId.new()
    uow.schedule_fire_delivery_plan_repo.add(
        ScheduleFireDeliveryPlan.ready(
            id=ScheduleFireDeliveryPlanId.new(),
            schedule_fire_id=fire_id,
            schedule_id=ScheduleId.new(),
            execution_id=run.execution_id,
            route_id="scheduled-route",
            route_fingerprint="a" * 64,
            route_max_body_chars=route_max_body_chars,
            created_at=created_at,
        )
    )
    return fire_id


def _finished(uow: FakeUnitOfWork, run: Run, payload: JsonValue, sequence: int = 1) -> None:
    uow.event_store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run.id,
            type=RunEventType.AGENT_FINISHED,
            sequence=sequence,
            occurred_at=T0,
            payload=payload,
        )
    )


def test_materialize_scheduled_answer_uses_canonical_root_summary_once() -> None:
    uow = FakeUnitOfWork()
    run = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    run.start(T0)
    run.succeed(NOW)
    uow.runs.add(run)
    fire_id = _ready_plan(uow, run)
    _finished(uow, run, {"summary": "  hello\r\nworld  ", "details": {"secret": "never"}})

    materializer = MaterializeScheduledAnswerDeliveries(
        CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
    )
    assert materializer.execute() == 1
    assert materializer.execute() == 0
    delivery = next(iter(uow.delivery_repo.items.values()))
    assert delivery.source_kind is DeliverySourceKind.SCHEDULED_RUN_ANSWER
    assert delivery.source_run_id == run.id
    assert delivery.source_schedule_fire_id == fire_id
    assert delivery.route_id == "scheduled-route"
    assert delivery.route_fingerprint == "a" * 64
    assert delivery.body == "hello\nworld"
    normalized_body = "hello\nworld"
    assert delivery.body_sha256 == hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
    assert "never" not in delivery.body


def test_materialize_scheduled_answer_uses_latest_successful_retry_only() -> None:
    uow = FakeUnitOfWork()
    root = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    root.start(T0)
    root.fail(NOW, Failure("x", "failed", True, FailureCause.RUNTIME))
    retry = Run.new(id=RunId.new(), task_id=root.task_id, created_at=NOW, execution_id=root.id)
    retry.start(NOW)
    retry.succeed(NOW + LEASE)
    uow.runs.add(root)
    uow.runs.add(retry)
    _ready_plan(uow, root)
    _finished(uow, root, {"summary": "root must not escape"})
    _finished(uow, retry, {"summary": "retry answer"})

    assert (
        MaterializeScheduledAnswerDeliveries(
            CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
        ).execute()
        == 1
    )
    delivery = next(iter(uow.delivery_repo.items.values()))
    assert delivery.source_run_id == retry.id
    assert delivery.body == "retry answer"


def test_materialize_scheduled_answer_leaves_active_or_invalid_lineages_eligible() -> None:
    uow = FakeUnitOfWork()
    root = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    root.start(T0)
    root.fail(NOW, Failure("x", "failed", True, FailureCause.RUNTIME))
    retry = Run.new(id=RunId.new(), task_id=root.task_id, created_at=NOW, execution_id=root.id)
    uow.runs.add(root)
    uow.runs.add(retry)
    _ready_plan(uow, root)
    materializer = MaterializeScheduledAnswerDeliveries(
        CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=10
    )
    assert materializer.execute() == 0
    assert not uow.delivery_repo.items
    retry.start(NOW)
    retry.succeed(NOW + LEASE)
    _finished(uow, retry, {"summary": "\x00unsafe"})
    assert materializer.execute() == 0
    later_retry = Run.new(
        id=RunId.new(), task_id=root.task_id, created_at=NOW + LEASE, execution_id=root.id
    )
    later_retry.start(NOW + LEASE)
    later_retry.succeed(NOW + LEASE + timedelta(seconds=1))
    uow.runs.add(later_retry)
    _finished(uow, later_retry, {"summary": "safe", "details": {"claim_token": "nope"}})
    assert materializer.execute() == 1


def test_active_or_failed_head_does_not_consume_successful_candidate_batch() -> None:
    uow = FakeUnitOfWork()
    active = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    failed = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    valid = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    active.start(T0)
    failed.start(T0)
    failed.fail(NOW, Failure("x", "failed", False, FailureCause.RUNTIME))
    valid.start(T0)
    valid.succeed(NOW)
    for run in (active, failed, valid):
        uow.runs.add(run)
        _ready_plan(uow, run)
    _finished(uow, valid, {"summary": "later valid"})

    assert (
        MaterializeScheduledAnswerDeliveries(
            CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=1
        ).execute()
        == 1
    )
    delivery = next(iter(uow.delivery_repo.items.values()))
    assert delivery.source_run_id == valid.id


def test_rejected_success_is_durable_and_frozen_route_bound_is_enforced() -> None:
    uow = FakeUnitOfWork()
    rejected = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    valid = Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)
    for run in (rejected, valid):
        run.start(T0)
        run.succeed(NOW)
        uow.runs.add(run)
    rejected_fire = _ready_plan(uow, rejected, route_max_body_chars=4)
    _ready_plan(uow, valid, created_at=T0 + timedelta(seconds=1))
    _finished(uow, rejected, {"summary": "five!", "details": {"token": "SENTINEL"}})
    _finished(uow, valid, {"summary": "valid"})
    materializer = MaterializeScheduledAnswerDeliveries(
        CountingUnitOfWorkFactory(uow), FakeClock(NOW), batch_size=1
    )

    assert materializer.execute() == 0
    rejected_plan = uow.schedule_fire_delivery_plan_repo.get_by_fire(rejected_fire)
    assert rejected_plan is not None
    assert rejected_plan.content_rejected_run_id == rejected.id
    assert materializer.execute() == 1
    delivery = next(iter(uow.delivery_repo.items.values()))
    assert delivery.body == "valid"
    assert "SENTINEL" not in delivery.body


class TestScheduledAnswerContentGate:
    @pytest.mark.parametrize(
        "control",
        ["\x00", "\x1f", "\x7f", "\u0085"],
        ids=["U+0000", "U+001F", "U+007F", "U+0085"],
    )
    def test_rejects_unicode_control_characters_except_lf(self, control: str) -> None:
        assert ScheduledAnswerContentGate().validate(f"pre{control}post", 16000) is None

    def test_preserves_lf_line_breaks(self) -> None:
        assert ScheduledAnswerContentGate().validate("line one\nline two", 16000) == (
            "line one\nline two"
        )

    def test_accepts_ordinary_unicode_user_facing_text(self) -> None:
        summary = "Café — 東京 中文 مرحبا 🚀"
        assert ScheduledAnswerContentGate().validate(summary, 16000) == summary

    def test_rejects_unpaired_utf16_surrogate(self) -> None:
        assert ScheduledAnswerContentGate().validate("pre\ud800post", 16000) is None

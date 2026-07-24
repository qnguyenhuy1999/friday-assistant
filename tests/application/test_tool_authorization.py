"""Exact-action approval binding: fingerprint determinism and sensitivity,
the authorization matrix (only APPROVED + exact match + unconsumed), and
claim-aware approval creation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from friday.application.commands import RequestApprovalCommand
from friday.application.errors import ClaimLost, EntityConflict
from friday.application.tool_authorization import (
    RequestToolApproval,
    compute_authorization_fingerprint,
    find_authorizing_approval,
)
from friday.application.tool_gateway import ToolCall
from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.event import RunEventType
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId, TaskId
from friday.domain.run import Run, RunStatus
from friday.domain.task import Task
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

RUN_ID = RunId.parse("22222222-2222-2222-2222-222222222222")
STEP_ID = RunStepId.parse("33333333-3333-3333-3333-333333333333")
CALL = ToolCall(tool="workspace.write_text", tool_input={"path": "a.txt", "content": "x"})


def fingerprint_of(
    run_id: RunId = RUN_ID, step_id: RunStepId | None = None, call: ToolCall = CALL
) -> str:
    return compute_authorization_fingerprint(run_id=run_id, step_id=step_id, call=call)


# --- fingerprint ----------------------------------------------------------


def test_fingerprint_is_64_hex_chars_and_deterministic() -> None:
    first = fingerprint_of()
    second = fingerprint_of()
    assert first == second
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


def test_fingerprint_is_input_key_order_independent() -> None:
    call_a = ToolCall(tool="workspace.write_text", tool_input={"path": "a.txt", "content": "x"})
    call_b = ToolCall(tool="workspace.write_text", tool_input={"content": "x", "path": "a.txt"})
    assert fingerprint_of(call=call_a) == fingerprint_of(call=call_b)


def test_fingerprint_changes_with_every_bound_dimension() -> None:
    base = fingerprint_of()
    assert fingerprint_of(run_id=RunId.new()) != base
    assert fingerprint_of(step_id=STEP_ID) != base
    assert (
        fingerprint_of(call=ToolCall(tool="workspace.read_text", tool_input=CALL.tool_input))
        != base
    )
    assert (
        fingerprint_of(
            call=ToolCall(tool="workspace.write_text", tool_input={"path": "b.txt", "content": "x"})
        )
        != base
    )


# --- authorization matrix -------------------------------------------------


def _approval(
    *,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    fingerprint: str | None = None,
    consumed: bool = False,
) -> ApprovalRequest:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RUN_ID,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="",
        requested_action=CALL.tool,
        requested_input=CALL.tool_input,
        requested_at=T0,
        authorization_fingerprint=fingerprint if fingerprint is not None else fingerprint_of(),
    )
    if status is ApprovalStatus.APPROVED:
        approval.approve(T0, resolver="patrick")
    elif status is ApprovalStatus.REJECTED:
        approval.reject(T0, resolver="patrick")
    elif status is ApprovalStatus.CANCELLED:
        approval.cancel(T0)
    elif status is ApprovalStatus.EXPIRED:
        approval.expire(T0)
    if consumed:
        approval.consume(T0)
    return approval


def test_approved_exact_match_authorizes() -> None:
    approval = _approval()
    assert find_authorizing_approval([approval], fingerprint=fingerprint_of()) is approval


@pytest.mark.parametrize(
    "status",
    [
        ApprovalStatus.PENDING,
        ApprovalStatus.REJECTED,
        ApprovalStatus.CANCELLED,
        ApprovalStatus.EXPIRED,
    ],
)
def test_non_approved_statuses_never_authorize(status: ApprovalStatus) -> None:
    approval = _approval(status=status)
    assert find_authorizing_approval([approval], fingerprint=fingerprint_of()) is None


def test_consumed_approval_never_authorizes_again() -> None:
    approval = _approval(consumed=True)
    assert find_authorizing_approval([approval], fingerprint=fingerprint_of()) is None


def test_different_fingerprint_never_authorizes() -> None:
    approval = _approval(fingerprint="b" * 64)
    assert find_authorizing_approval([approval], fingerprint=fingerprint_of()) is None


def test_legacy_approval_without_fingerprint_never_authorizes() -> None:
    legacy = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RUN_ID,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="",
        requested_action=CALL.tool,
        requested_input=CALL.tool_input,
        requested_at=T0,
    )
    legacy.approve(T0, resolver="patrick")
    assert find_authorizing_approval([legacy], fingerprint=fingerprint_of()) is None


# --- claim-aware approval creation ---------------------------------------


LEASE = timedelta(seconds=60)


def _claimed_run() -> tuple[FakeUnitOfWork, CountingUnitOfWorkFactory, Run, int]:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    uow.task_repo.add(task)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    run.start(T0)
    uow.run_repo.add(run)
    uow.work_queue_repo.enqueue(run.id, available_at=T0, enqueued_at=T0)
    assert uow.work_queue_repo.try_claim(run.id, "w1", "tok", T0, T0 + LEASE)
    item = uow.work_queue_repo.get(run.id)
    assert item is not None
    return uow, factory, run, item.claim_generation


def _command(run: Run) -> RequestApprovalCommand:
    return RequestApprovalCommand(
        run_id=run.id,
        category=ApprovalCategory.FILESYSTEM_WRITE,
        summary="write a.txt",
        reason="model requested it",
        requested_action=CALL.tool,
        requested_input=CALL.tool_input,
        authorization_fingerprint=fingerprint_of(run_id=run.id),
    )


def test_active_claim_creates_waiting_approval_with_fingerprint() -> None:
    uow, factory, run, generation = _claimed_run()
    use_case = RequestToolApproval(factory, FakeClock(T0 + timedelta(seconds=1)))
    result = use_case.execute(
        _command(run), worker_id="w1", claim_token="tok", claim_generation=generation
    )
    assert result.authorization_fingerprint == fingerprint_of(run_id=run.id)
    assert run.status is RunStatus.WAITING_FOR_APPROVAL
    assert run.approval_request_id == result.approval_id
    assert uow.work_queue_repo.get(run.id) is None  # work item parked
    assert uow.commit_count == 1
    event_types = [event.type for event in uow.event_store.list_for_run(run.id)]
    assert event_types == [
        RunEventType.APPROVAL_REQUESTED,
        RunEventType.RUN_WAITING_FOR_APPROVAL,
    ]


def test_inactive_claim_persists_nothing() -> None:
    uow, factory, run, generation = _claimed_run()
    use_case = RequestToolApproval(factory, FakeClock(T0 + timedelta(seconds=1)))
    with pytest.raises(ClaimLost):
        use_case.execute(
            _command(run), worker_id="w2", claim_token="wrong", claim_generation=generation
        )
    assert run.status is RunStatus.RUNNING
    assert uow.approval_repo.list_for_run(run.id) == []
    assert uow.commit_count == 0
    assert uow.work_queue_repo.get(run.id) is not None  # work item intact


def test_expired_lease_persists_nothing() -> None:
    uow, factory, run, generation = _claimed_run()
    late = FakeClock(T0 + LEASE + timedelta(seconds=1))
    use_case = RequestToolApproval(factory, late)
    with pytest.raises(ClaimLost):
        use_case.execute(
            _command(run), worker_id="w1", claim_token="tok", claim_generation=generation
        )
    assert run.status is RunStatus.RUNNING
    assert uow.commit_count == 0


def test_claim_lost_at_removal_aborts_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    uow, factory, run, generation = _claimed_run()

    def deny_removal(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(uow.work_queue_repo, "remove_if_claimed", deny_removal)
    use_case = RequestToolApproval(factory, FakeClock(T0 + timedelta(seconds=1)))
    with pytest.raises(ClaimLost):
        use_case.execute(
            _command(run), worker_id="w1", claim_token="tok", claim_generation=generation
        )
    assert uow.commit_count == 0
    assert uow.rollback_count == 1


def test_second_request_under_parked_claim_is_claim_lost() -> None:
    uow, factory, run, generation = _claimed_run()
    use_case = RequestToolApproval(factory, FakeClock(T0 + timedelta(seconds=1)))
    use_case.execute(_command(run), worker_id="w1", claim_token="tok", claim_generation=generation)
    # the work item was parked with the first approval — the claim is gone
    with pytest.raises(ClaimLost):
        use_case.execute(
            _command(run), worker_id="w1", claim_token="tok", claim_generation=generation
        )


def test_existing_pending_approval_conflicts() -> None:
    uow, factory, run, generation = _claimed_run()
    pending = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run.id,
        category=ApprovalCategory.OTHER,
        summary="already pending",
        reason="",
        requested_action="other",
        requested_input=None,
        requested_at=T0,
    )
    uow.approval_repo.add(pending)
    use_case = RequestToolApproval(factory, FakeClock(T0 + timedelta(seconds=1)))
    with pytest.raises(EntityConflict):
        use_case.execute(
            _command(run), worker_id="w1", claim_token="tok", claim_generation=generation
        )
    assert run.status is RunStatus.RUNNING
    assert uow.commit_count == 0


def test_utc_timestamps_in_result() -> None:
    uow, factory, run, generation = _claimed_run()
    moment = T0 + timedelta(seconds=5)
    use_case = RequestToolApproval(factory, FakeClock(moment))
    result = use_case.execute(
        _command(run), worker_id="w1", claim_token="tok", claim_generation=generation
    )
    assert result.requested_at == moment
    assert result.requested_at.tzinfo == UTC
    assert isinstance(result.requested_at, datetime)

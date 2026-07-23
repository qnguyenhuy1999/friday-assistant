"""Run lifecycle: allowed/forbidden transitions, terminal-state rejection,
end-timestamp-precedes-start invariant."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import TERMINAL_RUN_STATUSES, Run, RunStatus

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)
BEFORE_T0 = datetime(2025, 12, 31, tzinfo=UTC)

ALL_STATUSES = list(RunStatus)


def _new_run() -> Run:
    return Run.new(id=RunId.new(), task_id=TaskId.new(), created_at=T0)


def _failure() -> Failure:
    return Failure(code="x", message="boom", retryable=False, cause=FailureCause.INTERNAL)


def _run_in(status: RunStatus) -> Run:
    run = _new_run()
    if status is RunStatus.QUEUED:
        return run
    run.start(T0)
    if status is RunStatus.RUNNING:
        return run
    if status is RunStatus.WAITING_FOR_APPROVAL:
        run.wait_for_approval(T0, ApprovalRequestId.new())
        return run
    if status is RunStatus.SUCCEEDED:
        run.succeed(T1)
    elif status is RunStatus.FAILED:
        run.fail(T1, _failure())
    elif status is RunStatus.CANCELLED:
        run.cancel(T1)
    return run


def test_start_from_queued_succeeds() -> None:
    run = _run_in(RunStatus.QUEUED)
    run.start(T1)
    assert run.status is RunStatus.RUNNING
    assert run.started_at == T1


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != RunStatus.QUEUED])
def test_start_from_non_queued_is_rejected(status: RunStatus) -> None:
    run = _run_in(status)
    with pytest.raises(InvalidStateTransition):
        run.start(T1)


def test_wait_for_approval_from_running_succeeds() -> None:
    run = _run_in(RunStatus.RUNNING)
    approval_id = ApprovalRequestId.new()
    run.wait_for_approval(T1, approval_id)
    assert run.status is RunStatus.WAITING_FOR_APPROVAL
    assert run.approval_request_id == approval_id


def test_resume_from_waiting_for_approval_clears_approval_id() -> None:
    run = _run_in(RunStatus.WAITING_FOR_APPROVAL)
    run.resume(T1)
    assert run.status is RunStatus.RUNNING
    assert run.approval_request_id is None


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != RunStatus.WAITING_FOR_APPROVAL])
def test_resume_from_non_waiting_is_rejected(status: RunStatus) -> None:
    run = _run_in(status)
    with pytest.raises(InvalidStateTransition):
        run.resume(T1)


def test_succeed_from_running_succeeds() -> None:
    run = _run_in(RunStatus.RUNNING)
    run.succeed(T1)
    assert run.status is RunStatus.SUCCEEDED
    assert run.ended_at == T1


def test_succeed_end_before_start_is_rejected() -> None:
    run = _run_in(RunStatus.RUNNING)
    with pytest.raises(DomainValidationError):
        run.succeed(BEFORE_T0)


def test_fail_end_before_start_is_rejected() -> None:
    run = _run_in(RunStatus.RUNNING)
    with pytest.raises(DomainValidationError):
        run.fail(BEFORE_T0, _failure())


@pytest.mark.parametrize(
    "status", [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_FOR_APPROVAL]
)
def test_cancel_from_non_terminal_status_succeeds(status: RunStatus) -> None:
    run = _run_in(status)
    run.cancel(T1)
    assert run.status is RunStatus.CANCELLED
    assert run.approval_request_id is None


@pytest.mark.parametrize("status", sorted(TERMINAL_RUN_STATUSES, key=str))
def test_terminal_statuses_are_actually_terminal(status: RunStatus) -> None:
    run = _run_in(status)
    with pytest.raises(InvalidStateTransition):
        run.start(T1)
    with pytest.raises(InvalidStateTransition):
        run.succeed(T1)
    with pytest.raises(InvalidStateTransition):
        run.fail(T1, _failure())
    with pytest.raises(InvalidStateTransition):
        run.cancel(T1)

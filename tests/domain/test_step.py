"""RunStep lifecycle: allowed/forbidden transitions, skip-only-from-pending,
field validation, end-timestamp invariant."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, RunStepId
from friday.domain.step import TERMINAL_RUN_STEP_STATUSES, RunStep, RunStepStatus

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)
BEFORE_T0 = datetime(2025, 12, 31, tzinfo=UTC)

ALL_STATUSES = list(RunStepStatus)


def _new_step() -> RunStep:
    return RunStep.new(
        id=RunStepId.new(), run_id=RunId.new(), name="  step  ", position=0, created_at=T0
    )


def _failure() -> Failure:
    return Failure(code="x", message="boom", retryable=False, cause=FailureCause.INTERNAL)


def _step_in(status: RunStepStatus) -> RunStep:
    step = _new_step()
    if status is RunStepStatus.PENDING:
        return step
    if status is RunStepStatus.SKIPPED:
        step.skip(T1)
        return step
    step.start(T0)
    if status is RunStepStatus.RUNNING:
        return step
    if status is RunStepStatus.WAITING_FOR_APPROVAL:
        step.wait_for_approval(T0, ApprovalRequestId.new())
        return step
    if status is RunStepStatus.SUCCEEDED:
        step.succeed(T1)
    elif status is RunStepStatus.FAILED:
        step.fail(T1, _failure())
    elif status is RunStepStatus.CANCELLED:
        step.cancel(T1)
    return step


def test_new_strips_name() -> None:
    step = _new_step()
    assert step.name == "step"
    assert step.status is RunStepStatus.PENDING


def test_new_rejects_blank_name() -> None:
    with pytest.raises(DomainValidationError):
        RunStep.new(id=RunStepId.new(), run_id=RunId.new(), name="  ", position=0, created_at=T0)


def test_new_rejects_negative_position() -> None:
    with pytest.raises(DomainValidationError):
        RunStep.new(id=RunStepId.new(), run_id=RunId.new(), name="s", position=-1, created_at=T0)


def test_start_from_pending_succeeds() -> None:
    step = _step_in(RunStepStatus.PENDING)
    step.start(T1)
    assert step.status is RunStepStatus.RUNNING


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != RunStepStatus.PENDING])
def test_start_from_non_pending_is_rejected(status: RunStepStatus) -> None:
    step = _step_in(status)
    with pytest.raises(InvalidStateTransition):
        step.start(T1)


def test_skip_from_pending_succeeds() -> None:
    step = _step_in(RunStepStatus.PENDING)
    step.skip(T1)
    assert step.status is RunStepStatus.SKIPPED


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != RunStepStatus.PENDING])
def test_skip_from_non_pending_is_rejected(status: RunStepStatus) -> None:
    step = _step_in(status)
    with pytest.raises(InvalidStateTransition):
        step.skip(T1)


def test_succeed_end_before_start_is_rejected() -> None:
    step = _step_in(RunStepStatus.RUNNING)
    with pytest.raises(DomainValidationError):
        step.succeed(BEFORE_T0)


@pytest.mark.parametrize(
    "status",
    [RunStepStatus.PENDING, RunStepStatus.RUNNING, RunStepStatus.WAITING_FOR_APPROVAL],
)
def test_cancel_from_non_terminal_status_succeeds(status: RunStepStatus) -> None:
    step = _step_in(status)
    step.cancel(T1)
    assert step.status is RunStepStatus.CANCELLED


@pytest.mark.parametrize("status", sorted(TERMINAL_RUN_STEP_STATUSES, key=str))
def test_terminal_statuses_are_actually_terminal(status: RunStepStatus) -> None:
    step = _step_in(status)
    with pytest.raises(InvalidStateTransition):
        step.start(T1)
    with pytest.raises(InvalidStateTransition):
        step.cancel(T1)

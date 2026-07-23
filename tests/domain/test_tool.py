"""ToolInvocation lifecycle: allowed/forbidden transitions, explicit-output
requirement on success (None is a valid output, unset is not), end-timestamp
invariant."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunId, ToolInvocationId
from friday.domain.tool import (
    TERMINAL_TOOL_INVOCATION_STATUSES,
    ToolInvocation,
    ToolInvocationStatus,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)
BEFORE_T0 = datetime(2025, 12, 31, tzinfo=UTC)

ALL_STATUSES = list(ToolInvocationStatus)


def _new_invocation() -> ToolInvocation:
    return ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=RunId.new(),
        tool_name="  search  ",
        requested_input={"q": "x"},
        requested_at=T0,
    )


def _failure() -> Failure:
    return Failure(code="x", message="boom", retryable=False, cause=FailureCause.INTERNAL)


def _invocation_in(status: ToolInvocationStatus) -> ToolInvocation:
    invocation = _new_invocation()
    if status is ToolInvocationStatus.REQUESTED:
        return invocation
    invocation.start(T0)
    if status is ToolInvocationStatus.RUNNING:
        return invocation
    if status is ToolInvocationStatus.SUCCEEDED:
        invocation.succeed(T1, output={"result": 1})
    elif status is ToolInvocationStatus.FAILED:
        invocation.fail(T1, _failure())
    elif status is ToolInvocationStatus.CANCELLED:
        invocation.cancel(T1)
    return invocation


def test_new_strips_tool_name() -> None:
    invocation = _new_invocation()
    assert invocation.tool_name == "search"
    assert invocation.status is ToolInvocationStatus.REQUESTED


def test_new_rejects_blank_tool_name() -> None:
    with pytest.raises(DomainValidationError):
        ToolInvocation.new(
            id=ToolInvocationId.new(),
            run_id=RunId.new(),
            tool_name="  ",
            requested_input=None,
            requested_at=T0,
        )


def test_succeed_requires_explicit_output() -> None:
    invocation = _invocation_in(ToolInvocationStatus.RUNNING)
    with pytest.raises(DomainValidationError):
        invocation.succeed(T1)


def test_succeed_accepts_none_as_explicit_output() -> None:
    invocation = _invocation_in(ToolInvocationStatus.RUNNING)
    invocation.succeed(T1, output=None)
    assert invocation.output_set is True
    assert invocation.output is None
    assert invocation.status is ToolInvocationStatus.SUCCEEDED


def test_succeed_end_before_start_is_rejected() -> None:
    invocation = _invocation_in(ToolInvocationStatus.RUNNING)
    with pytest.raises(DomainValidationError):
        invocation.succeed(BEFORE_T0, output={})


@pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != ToolInvocationStatus.RUNNING])
def test_succeed_from_non_running_is_rejected(status: ToolInvocationStatus) -> None:
    invocation = _invocation_in(status)
    with pytest.raises(InvalidStateTransition):
        invocation.succeed(T1, output={})


@pytest.mark.parametrize("status", [ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RUNNING])
def test_cancel_from_non_terminal_status_succeeds(status: ToolInvocationStatus) -> None:
    invocation = _invocation_in(status)
    invocation.cancel(T1)
    assert invocation.status is ToolInvocationStatus.CANCELLED


@pytest.mark.parametrize("status", sorted(TERMINAL_TOOL_INVOCATION_STATUSES, key=str))
def test_terminal_statuses_are_actually_terminal(status: ToolInvocationStatus) -> None:
    invocation = _invocation_in(status)
    with pytest.raises(InvalidStateTransition):
        invocation.start(T1)
    with pytest.raises(InvalidStateTransition):
        invocation.cancel(T1)

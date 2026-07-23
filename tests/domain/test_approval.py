"""ApprovalRequest lifecycle: pending-only resolution, field validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.approval import (
    TERMINAL_APPROVAL_STATUSES,
    ApprovalCategory,
    ApprovalRequest,
    ApprovalStatus,
)
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import ApprovalRequestId, RunId

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)

ALL_STATUSES = list(ApprovalStatus)


def _new_request() -> ApprovalRequest:
    return ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RunId.new(),
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="  run rm -rf  ",
        reason="cleanup",
        requested_action="  delete files  ",
        requested_input={"path": "/tmp"},
        requested_at=T0,
    )


def _request_in(status: ApprovalStatus) -> ApprovalRequest:
    request = _new_request()
    if status is ApprovalStatus.PENDING:
        return request
    if status is ApprovalStatus.APPROVED:
        request.approve(T1, resolver="alice")
    elif status is ApprovalStatus.REJECTED:
        request.reject(T1, resolver="alice")
    elif status is ApprovalStatus.CANCELLED:
        request.cancel(T1)
    elif status is ApprovalStatus.EXPIRED:
        request.expire(T1)
    return request


def test_new_strips_summary_and_action() -> None:
    request = _new_request()
    assert request.summary == "run rm -rf"
    assert request.requested_action == "delete files"
    assert request.status is ApprovalStatus.PENDING


def test_new_rejects_blank_summary() -> None:
    with pytest.raises(DomainValidationError):
        ApprovalRequest.new(
            id=ApprovalRequestId.new(),
            run_id=RunId.new(),
            category=ApprovalCategory.OTHER,
            summary="   ",
            reason="r",
            requested_action="a",
            requested_input=None,
            requested_at=T0,
        )


def test_new_rejects_blank_requested_action() -> None:
    with pytest.raises(DomainValidationError):
        ApprovalRequest.new(
            id=ApprovalRequestId.new(),
            run_id=RunId.new(),
            category=ApprovalCategory.OTHER,
            summary="s",
            reason="r",
            requested_action="   ",
            requested_input=None,
            requested_at=T0,
        )


def test_approve_from_pending_succeeds() -> None:
    request = _request_in(ApprovalStatus.PENDING)
    request.approve(T1, resolver="alice", resolution_note="looks fine")
    assert request.status is ApprovalStatus.APPROVED
    assert request.resolver == "alice"
    assert request.resolution_note == "looks fine"


def test_reject_from_pending_succeeds() -> None:
    request = _request_in(ApprovalStatus.PENDING)
    request.reject(T1, resolver="bob")
    assert request.status is ApprovalStatus.REJECTED


def test_cancel_clears_resolver() -> None:
    request = _request_in(ApprovalStatus.PENDING)
    request.cancel(T1, resolution_note="no longer needed")
    assert request.status is ApprovalStatus.CANCELLED
    assert request.resolver is None


def test_expire_from_pending_succeeds() -> None:
    request = _request_in(ApprovalStatus.PENDING)
    request.expire(T1)
    assert request.status is ApprovalStatus.EXPIRED


@pytest.mark.parametrize("status", sorted(TERMINAL_APPROVAL_STATUSES, key=str))
def test_terminal_statuses_reject_further_resolution(status: ApprovalStatus) -> None:
    request = _request_in(status)
    with pytest.raises(InvalidStateTransition):
        request.approve(T1, resolver="alice")
    with pytest.raises(InvalidStateTransition):
        request.reject(T1, resolver="alice")
    with pytest.raises(InvalidStateTransition):
        request.cancel(T1)
    with pytest.raises(InvalidStateTransition):
        request.expire(T1)


def test_new_accepts_future_expiry_and_normalizes_to_utc() -> None:
    request = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RunId.new(),
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=T0,
        expires_at=T1,
    )
    assert request.expires_at == T1
    assert request.expires_at is not None and request.expires_at.tzinfo is UTC


def test_new_defaults_to_no_expiry() -> None:
    assert _new_request().expires_at is None


@pytest.mark.parametrize("expiry", [T0, datetime(2025, 12, 31, tzinfo=UTC)])
def test_new_rejects_expiry_not_after_requested_at(expiry: datetime) -> None:
    with pytest.raises(DomainValidationError):
        ApprovalRequest.new(
            id=ApprovalRequestId.new(),
            run_id=RunId.new(),
            category=ApprovalCategory.TOOL_EXECUTION,
            summary="s",
            reason="r",
            requested_action="a",
            requested_input=None,
            requested_at=T0,
            expires_at=expiry,
        )


def test_new_rejects_naive_expiry() -> None:
    with pytest.raises(DomainValidationError):
        ApprovalRequest.new(
            id=ApprovalRequestId.new(),
            run_id=RunId.new(),
            category=ApprovalCategory.TOOL_EXECUTION,
            summary="s",
            reason="r",
            requested_action="a",
            requested_input=None,
            requested_at=T0,
            expires_at=T1.replace(tzinfo=None),
        )

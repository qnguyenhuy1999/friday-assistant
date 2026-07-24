"""ApprovalRequest authorization binding: fingerprint storage and one-shot
consumption semantics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import ApprovalRequestId, RunId

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FINGERPRINT = "a" * 64


def _approval(fingerprint: str | None = FINGERPRINT) -> ApprovalRequest:
    return ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=RunId.new(),
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="run a protected tool",
        reason="",
        requested_action="workspace.write_text",
        requested_input={"path": "a.txt"},
        requested_at=NOW,
        authorization_fingerprint=fingerprint,
    )


def test_new_stores_fingerprint() -> None:
    approval = _approval()
    assert approval.authorization_fingerprint == FINGERPRINT
    assert approval.consumed_at is None
    assert approval.is_consumed is False


def test_fingerprint_defaults_to_none() -> None:
    assert _approval(fingerprint=None).authorization_fingerprint is None


def test_consume_records_utc_timestamp_once() -> None:
    approval = _approval()
    approval.approve(NOW, resolver="patrick")
    approval.consume(NOW)
    assert approval.consumed_at == NOW
    assert approval.is_consumed is True


def test_consume_requires_approved_status() -> None:
    pending = _approval()
    with pytest.raises(InvalidStateTransition):
        pending.consume(NOW)

    rejected = _approval()
    rejected.reject(NOW, resolver="patrick")
    with pytest.raises(InvalidStateTransition):
        rejected.consume(NOW)

    expired = _approval()
    expired.expire(NOW)
    with pytest.raises(InvalidStateTransition):
        expired.consume(NOW)


def test_consume_is_one_shot() -> None:
    approval = _approval()
    approval.approve(NOW, resolver="patrick")
    approval.consume(NOW)
    with pytest.raises(DomainValidationError):
        approval.consume(NOW)

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from friday.domain import (
    ApprovalCategory,
    ApprovalRequest,
    ApprovalRequestId,
    Run,
    RunId,
    Task,
    TaskId,
)
from friday.infrastructure.persistence.repositories import (
    ApprovalRepository,
    RunRepository,
    TaskRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(session) -> RunId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return run.id


def _make_approval(run_id: RunId, requested_at: datetime) -> ApprovalRequest:
    return ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=requested_at,
    )


def test_add_then_get_round_trips(session) -> None:
    run_id = _make_run(session)
    repo = ApprovalRepository(session)
    approval = _make_approval(run_id, T0)
    repo.add(approval)
    session.flush()
    fetched = repo.get(approval.id)
    assert fetched is not None
    assert fetched.id == approval.id
    assert fetched.run_id == approval.run_id


def test_get_returns_none_for_missing_id(session) -> None:
    repo = ApprovalRepository(session)
    assert repo.get(ApprovalRequestId.new()) is None


def test_save_persists_status_transition(session) -> None:
    run_id = _make_run(session)
    repo = ApprovalRepository(session)
    approval = _make_approval(run_id, T0)
    repo.add(approval)
    session.flush()
    approval.approve(T0, resolver="user")
    repo.save(approval)
    session.flush()
    fetched = repo.get(approval.id)
    assert fetched is not None
    assert fetched.status == approval.status


def test_list_pending_for_run_orders_by_requested_at_then_id_and_excludes_resolved(
    session,
) -> None:
    run_id = _make_run(session)
    repo = ApprovalRepository(session)
    pending_b = _make_approval(run_id, T0 + timedelta(seconds=1))
    pending_a = _make_approval(run_id, T0)
    approved = _make_approval(run_id, T0)
    approved.approve(T0, resolver="user")
    repo.add(pending_b)
    repo.add(pending_a)
    repo.add(approved)
    session.flush()
    result = repo.list_pending_for_run(run_id)
    assert [a.id for a in result] == [pending_a.id, pending_b.id]

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from friday.domain import Run, RunId, Task, TaskId
from friday.infrastructure.persistence.repositories import RunRepository, TaskRepository
from friday.infrastructure.persistence.work_queue import SqlAlchemyRunWorkQueue

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(session: Session) -> RunId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return run.id


def test_enqueue_creates_a_row_when_none_exists(session: Session) -> None:
    run_id = _make_run(session)
    queue = SqlAlchemyRunWorkQueue(session)
    queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
    session.flush()
    item = queue.get(run_id)
    assert item is not None
    assert item.run_id == run_id
    assert item.available_at == T0
    assert item.enqueued_at == T0
    assert item.claim_generation == 0
    assert item.claimed_by is None
    assert item.claim_token is None
    assert item.claimed_at is None
    assert item.heartbeat_at is None
    assert item.lease_expires_at is None


def test_enqueue_updates_available_and_enqueued_at_when_unclaimed(session: Session) -> None:
    run_id = _make_run(session)
    queue = SqlAlchemyRunWorkQueue(session)
    queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
    session.flush()
    later = T0 + timedelta(minutes=5)
    queue.enqueue(run_id, available_at=later, enqueued_at=later)
    session.flush()
    item = queue.get(run_id)
    assert item is not None
    assert item.available_at == later
    assert item.enqueued_at == later


def test_enqueue_leaves_claim_fields_untouched_when_actively_claimed(session: Session) -> None:
    run_id = _make_run(session)
    queue = SqlAlchemyRunWorkQueue(session)
    queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
    session.flush()
    lease_expires = T0 + timedelta(minutes=1)
    row = queue.get(run_id)
    assert row is not None
    from friday.infrastructure.persistence.models import RunWorkItemRow

    orm_row = session.get(RunWorkItemRow, str(run_id))
    assert orm_row is not None
    orm_row.claimed_by = "worker-1"
    orm_row.claim_token = "token-1"
    orm_row.lease_expires_at = lease_expires
    session.flush()

    later = T0 + timedelta(minutes=5)
    queue.enqueue(run_id, available_at=later, enqueued_at=later)
    session.flush()

    item = queue.get(run_id)
    assert item is not None
    assert item.available_at == later
    assert item.enqueued_at == later
    assert item.claimed_by == "worker-1"
    assert item.claim_token == "token-1"
    assert item.lease_expires_at == lease_expires


def test_get_returns_none_for_missing_run(session: Session) -> None:
    queue = SqlAlchemyRunWorkQueue(session)
    assert queue.get(RunId.new()) is None


def test_find_due_candidates_returns_only_claimable_rows_in_order(session: Session) -> None:
    from friday.infrastructure.persistence.models import RunWorkItemRow

    queue = SqlAlchemyRunWorkQueue(session)
    now = T0 + timedelta(hours=1)

    due_unclaimed = _make_run(session)
    queue.enqueue(due_unclaimed, available_at=T0, enqueued_at=T0 + timedelta(seconds=2))

    not_due = _make_run(session)
    queue.enqueue(not_due, available_at=now + timedelta(hours=1), enqueued_at=T0)

    claimed_lease_expired = _make_run(session)
    queue.enqueue(claimed_lease_expired, available_at=T0, enqueued_at=T0 + timedelta(seconds=1))
    session.flush()
    row = session.get(RunWorkItemRow, str(claimed_lease_expired))
    assert row is not None
    row.claimed_by = "worker-1"
    row.claim_token = "token-1"
    row.lease_expires_at = now - timedelta(minutes=1)

    claimed_active = _make_run(session)
    queue.enqueue(claimed_active, available_at=T0, enqueued_at=T0)
    session.flush()
    row2 = session.get(RunWorkItemRow, str(claimed_active))
    assert row2 is not None
    row2.claimed_by = "worker-2"
    row2.claim_token = "token-2"
    row2.lease_expires_at = now + timedelta(minutes=5)
    session.flush()

    candidates = queue.find_due_candidates(now, limit=10)
    result_ids = [c.run_id for c in candidates]
    assert result_ids == [claimed_lease_expired, due_unclaimed]

    limited = queue.find_due_candidates(now, limit=1)
    assert [c.run_id for c in limited] == [claimed_lease_expired]


def test_find_expired_claims_returns_only_expired_active_claims(session: Session) -> None:
    from friday.infrastructure.persistence.models import RunWorkItemRow

    queue = SqlAlchemyRunWorkQueue(session)
    now = T0 + timedelta(hours=1)

    unclaimed = _make_run(session)
    queue.enqueue(unclaimed, available_at=T0, enqueued_at=T0)

    not_yet_expired = _make_run(session)
    queue.enqueue(not_yet_expired, available_at=T0, enqueued_at=T0)
    session.flush()
    row = session.get(RunWorkItemRow, str(not_yet_expired))
    assert row is not None
    row.claimed_by = "worker-1"
    row.claim_token = "token-1"
    row.lease_expires_at = now + timedelta(minutes=5)

    expired_a = _make_run(session)
    queue.enqueue(expired_a, available_at=T0, enqueued_at=T0 + timedelta(seconds=1))
    session.flush()
    row_a = session.get(RunWorkItemRow, str(expired_a))
    assert row_a is not None
    row_a.claimed_by = "worker-2"
    row_a.claim_token = "token-2"
    row_a.lease_expires_at = now - timedelta(minutes=1)

    expired_b = _make_run(session)
    queue.enqueue(expired_b, available_at=T0, enqueued_at=T0)
    session.flush()
    row_b = session.get(RunWorkItemRow, str(expired_b))
    assert row_b is not None
    row_b.claimed_by = "worker-3"
    row_b.claim_token = "token-3"
    row_b.lease_expires_at = now - timedelta(minutes=2)
    session.flush()

    expired = queue.find_expired_claims(now, limit=10)
    assert [item.run_id for item in expired] == [expired_b, expired_a]

    limited = queue.find_expired_claims(now, limit=1)
    assert [item.run_id for item in limited] == [expired_b]


def test_remove_deletes_existing_row_and_is_noop_for_missing(session: Session) -> None:
    run_id = _make_run(session)
    queue = SqlAlchemyRunWorkQueue(session)
    queue.enqueue(run_id, available_at=T0, enqueued_at=T0)
    session.flush()
    assert queue.get(run_id) is not None

    queue.remove(run_id)
    session.flush()
    assert queue.get(run_id) is None

    queue.remove(RunId.new())
    session.flush()

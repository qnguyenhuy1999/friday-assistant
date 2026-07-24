from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from friday.domain import Run, RunId, Task, TaskId
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
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


def _sessions(tmp_path: Path) -> tuple[Session, Session, Session, Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'coordination.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    return factory(), factory(), factory(), engine


def test_claim_race_allows_only_first_committed_worker(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        run_id = _make_run(seed)
        SqlAlchemyRunWorkQueue(seed).enqueue(run_id, T0, T0)
        seed.commit()
        assert SqlAlchemyRunWorkQueue(session_a).try_claim(
            run_id, "worker-a", "token-a", T0, T0 + timedelta(minutes=1)
        )
        session_a.commit()
        assert not SqlAlchemyRunWorkQueue(session_b).try_claim(
            run_id, "worker-b", "token-b", T0, T0 + timedelta(minutes=1)
        )
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_multiple_workers_distribute_due_items_without_duplicate_claims(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        run_ids = [_make_run(seed) for _ in range(3)]
        seed_queue = SqlAlchemyRunWorkQueue(seed)
        for offset, run_id in enumerate(run_ids):
            seed_queue.enqueue(run_id, T0, T0 + timedelta(seconds=offset))
        seed.commit()

        queue_a, queue_b = SqlAlchemyRunWorkQueue(session_a), SqlAlchemyRunWorkQueue(session_b)
        first = queue_a.find_due_candidates(T0, 1)[0]
        assert queue_a.try_claim(
            first.run_id, "worker-a", "token-a1", T0, T0 + timedelta(minutes=1)
        )
        session_a.commit()
        second = queue_b.find_due_candidates(T0, 1)[0]
        assert queue_b.try_claim(
            second.run_id, "worker-b", "token-b1", T0, T0 + timedelta(minutes=1)
        )
        session_b.commit()
        third = queue_a.find_due_candidates(T0, 1)[0]
        assert queue_a.try_claim(
            third.run_id, "worker-a", "token-a2", T0, T0 + timedelta(minutes=1)
        )
        session_a.commit()

        seed.expire_all()
        claimed_by: dict[RunId, str | None] = {}
        for run_id in run_ids:
            item = SqlAlchemyRunWorkQueue(seed).get(run_id)
            assert item is not None
            claimed_by[run_id] = item.claimed_by
        assert {first.run_id, second.run_id, third.run_id} == set(run_ids)
        assert set(claimed_by.values()) == {"worker-a", "worker-b"}
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_renewal_is_valid_only_while_lease_is_strictly_in_the_future(tmp_path: Path) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        run_id = _make_run(seed)
        queue = SqlAlchemyRunWorkQueue(seed)
        queue.enqueue(run_id, T0, T0)
        first_expiry = T0 + timedelta(minutes=1)
        assert queue.try_claim(run_id, "worker", "token", T0, first_expiry)
        seed.commit()
        renewed_expiry = T0 + timedelta(minutes=3)
        assert SqlAlchemyRunWorkQueue(session_a).renew_lease(
            run_id, "worker", "token", 1, T0 + timedelta(seconds=30), renewed_expiry
        )
        session_a.commit()
        # Policy: renewal requires lease_expires_at > now; equality is expired.
        assert not SqlAlchemyRunWorkQueue(session_b).renew_lease(
            run_id, "worker", "token", 1, renewed_expiry, renewed_expiry + timedelta(minutes=1)
        )
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()


def test_expired_claim_recovery_increments_generation_and_fences_original_worker(
    tmp_path: Path,
) -> None:
    seed, session_a, session_b, engine = _sessions(tmp_path)
    try:
        run_id = _make_run(seed)
        queue = SqlAlchemyRunWorkQueue(seed)
        queue.enqueue(run_id, T0, T0)
        expires_at = T0 + timedelta(minutes=1)
        assert queue.try_claim(run_id, "worker-a", "token-a", T0, expires_at)
        seed.commit()

        recovered_at = expires_at + timedelta(seconds=1)
        queue_b = SqlAlchemyRunWorkQueue(session_b)
        assert queue_b.try_claim(
            run_id, "worker-b", "token-b", recovered_at, recovered_at + timedelta(minutes=1)
        )
        session_b.commit()
        session_a.expire_all()
        recovered = SqlAlchemyRunWorkQueue(session_a).get(run_id)
        assert recovered is not None and recovered.claim_generation == 2
        assert not SqlAlchemyRunWorkQueue(session_a).renew_lease(
            run_id, "worker-a", "token-a", 1, recovered_at, recovered_at + timedelta(minutes=1)
        )
        assert not SqlAlchemyRunWorkQueue(session_a).release_claim(run_id, "worker-a", "token-a", 1)
        assert not SqlAlchemyRunWorkQueue(session_a).remove_if_claimed(
            run_id, "worker-a", "token-a", 1
        )
    finally:
        seed.close()
        session_a.close()
        session_b.close()
        engine.dispose()

"""Phase 16 scheduler durability proofs against Alembic-migrated SQLite."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from friday.application.materialize_due_schedule import MaterializeDueSchedules
from friday.application.ports import UnitOfWorkFactory
from friday.application.schedule_lifecycle import CreateSchedule, CreateScheduleCommand
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.task import Task
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import RunWorkItemRow, ScheduleFireRow
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value


def _migrated_stack(tmp_path: Path) -> tuple[Path, Engine, UnitOfWorkFactory]:
    db_path = tmp_path / "schedules.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    return db_path, engine, create_unit_of_work_factory(create_session_factory(engine))


def _seed_due_schedule(factory: UnitOfWorkFactory, clock: FixedClock) -> Schedule:
    task = Task.new(id=TaskId.new(), title="scheduled", description="", created_at=T0)
    with factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    due = T0 + timedelta(minutes=1)
    schedule = CreateSchedule(factory, clock).execute(
        CreateScheduleCommand(task.id, ScheduleKind.ONCE, run_at=due, timezone="UTC")
    )
    clock.now_value = due
    return schedule


def _assert_one_materialized(
    engine: Engine, factory: UnitOfWorkFactory, schedule_id: ScheduleId
) -> None:
    with factory() as uow:
        fires = uow.schedule_fires.list_for_schedule(schedule_id, limit=10)
        assert len(fires) == 1
        assert uow.runs.get(fires[0].run_id) is not None
        assert uow.work_queue.get(fires[0].run_id) is not None
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ScheduleFireRow)) == 1
        assert connection.scalar(select(func.count()).select_from(RunWorkItemRow)) == 1


def test_two_real_scheduler_sessions_race_one_occurrence_to_one_fire_run_and_work_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database unique fence handles a real two-session race, not a fake UoW."""
    _, engine, factory = _migrated_stack(tmp_path)
    clock = FixedClock(T0)
    try:
        schedule = _seed_due_schedule(factory, clock)
        scheduler_a = MaterializeDueSchedules(factory, clock, batch_size=10)
        scheduler_b = MaterializeDueSchedules(factory, clock, batch_size=10)
        barrier = threading.Barrier(2)
        original = scheduler_a._materialize_one

        def synchronized_materialize(schedule_id: ScheduleId) -> bool:
            barrier.wait(timeout=5)
            return original(schedule_id)

        # Both independently-created scheduler objects reach their own
        # transaction after observing the same due occurrence.
        monkeypatch.setattr(scheduler_a, "_materialize_one", synchronized_materialize)
        monkeypatch.setattr(scheduler_b, "_materialize_one", synchronized_materialize)
        results: list[int] = []
        errors: list[BaseException] = []

        def execute(scheduler: MaterializeDueSchedules) -> None:
            try:
                results.append(scheduler.execute())
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=execute, args=(x,)) for x in (scheduler_a, scheduler_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(results) == [0, 1]
        _assert_one_materialized(engine, factory, schedule.id)
    finally:
        engine.dispose()


def test_materialization_and_queued_run_survive_engine_session_restart(tmp_path: Path) -> None:
    db_path, first_engine, first_factory = _migrated_stack(tmp_path)
    clock = FixedClock(T0)
    try:
        schedule = _seed_due_schedule(first_factory, clock)
        assert MaterializeDueSchedules(first_factory, clock, batch_size=10).execute() == 1
    finally:
        # Simulate scheduler process loss after committing Fire -> queued Run.
        first_engine.dispose()

    restarted_engine = create_engine(f"sqlite:///{db_path}")
    restarted_factory = create_unit_of_work_factory(create_session_factory(restarted_engine))
    try:
        # A fresh stack recovers the durable state without emitting another fire.
        assert MaterializeDueSchedules(restarted_factory, clock, batch_size=10).execute() == 0
        _assert_one_materialized(restarted_engine, restarted_factory, schedule.id)
    finally:
        restarted_engine.dispose()

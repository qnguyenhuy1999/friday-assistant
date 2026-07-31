"""Real SQLite proofs for scheduled final-answer delivery materialization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import UnitOfWorkFactory
from friday.application.schedule_lifecycle import CreateSchedule, CreateScheduleCommand
from friday.application.worker_maintenance import MaterializeScheduledAnswerDeliveries
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    RunId,
    ScheduleFireDeliveryPlanId,
    ScheduleFireId,
    TaskId,
)
from friday.domain.run import Run
from friday.domain.schedule import ScheduleKind
from friday.domain.schedule_fire import ScheduleFire
from friday.domain.schedule_fire_delivery_plan import ScheduleFireDeliveryPlan
from friday.domain.task import Task
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return T0


def _factory(tmp_path: Path) -> tuple[Engine, UnitOfWorkFactory]:
    db_path = tmp_path / "scheduled-answer.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    return engine, create_unit_of_work_factory(create_session_factory(engine))


def _seed_ready_plan(factory: UnitOfWorkFactory, *, retry: bool) -> tuple[ScheduleFireId, RunId]:
    task = Task.new(id=TaskId.new(), title="scheduled", description="", created_at=T0)
    with factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    schedule = CreateSchedule(factory, FixedClock()).execute(
        CreateScheduleCommand(task.id, ScheduleKind.ONCE, run_at=T0 + timedelta(minutes=1))
    )
    root = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    if retry:
        root.start(T0)
        root.fail(T0 + timedelta(seconds=1), Failure("x", "failed", True, FailureCause.RUNTIME))
        effective = Run.new(
            id=RunId.new(),
            task_id=task.id,
            created_at=T0 + timedelta(seconds=2),
            execution_id=root.id,
        )
        effective.start(T0 + timedelta(seconds=2))
        effective.succeed(T0 + timedelta(seconds=3))
    else:
        root.start(T0)
        root.succeed(T0 + timedelta(seconds=1))
        effective = root
    fire_id = ScheduleFireId.new()
    with factory() as uow:
        uow.runs.add(root)
        if retry:
            uow.runs.add(effective)
        fire = ScheduleFire.new(
            id=fire_id,
            schedule_id=schedule.id,
            scheduled_for=T0,
            fired_at=T0,
            run_id=root.id,
        )
        uow.schedule_fires.add(fire)
        uow.commit()
    with factory() as uow:
        uow.schedule_fire_delivery_plans.add_for_fire(
            ScheduleFireDeliveryPlan.ready(
                id=ScheduleFireDeliveryPlanId.new(),
                schedule_fire_id=fire.id,
                schedule_id=fire.schedule_id,
                execution_id=root.id,
                route_id="ops.primary",
                route_fingerprint="a" * 64,
                route_max_body_chars=16000,
                created_at=T0,
            ),
            fire,
        )
        uow.commit()
    with factory() as uow:
        LifecycleEvents.append_run_events(
            uow, effective, T0, [(RunEventType.AGENT_FINISHED, {"summary": "answer"}, None)]
        )
        uow.commit()
    return fire_id, effective.id


def test_sqlite_materializes_exactly_one_root_answer_delivery(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    try:
        fire_id, run_id = _seed_ready_plan(factory, retry=False)
        materializer = MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10)
        assert materializer.execute() == 1
        assert materializer.execute() == 0
        with factory() as uow:
            delivery = uow.deliveries.get_by_source_schedule_fire_id(fire_id)
        assert delivery is not None and delivery.source_run_id == run_id
    finally:
        engine.dispose()


def test_sqlite_materializes_retry_answer_from_effective_run(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    try:
        fire_id, retry_id = _seed_ready_plan(factory, retry=True)
        assert (
            MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
            == 1
        )
        with factory() as uow:
            delivery = uow.deliveries.get_by_source_schedule_fire_id(fire_id)
        assert delivery is not None and delivery.source_run_id == retry_id
    finally:
        engine.dispose()

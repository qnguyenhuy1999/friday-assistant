"""Real SQLite proofs for scheduled final-answer delivery materialization."""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import friday.infrastructure.persistence.unit_of_work as uow_mod
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import UnitOfWorkFactory
from friday.application.schedule_lifecycle import CreateSchedule, CreateScheduleCommand
from friday.application.worker_maintenance import MaterializeScheduledAnswerDeliveries
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import (
    DeliveryId,
    RunId,
    ScheduleFireDeliveryPlanId,
    ScheduleFireId,
    TaskId,
)
from friday.domain.run import Run, RunStatus
from friday.domain.schedule import ScheduleKind
from friday.domain.schedule_fire import ScheduleFire
from friday.domain.schedule_fire_delivery_plan import (
    ScheduleFireDeliveryContentSource,
    ScheduleFireDeliveryPlan,
    ScheduleFireDeliveryPlanStatus,
)
from friday.domain.task import Task
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import OutboundDeliveryRow
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


def _seed_case(
    factory: UnitOfWorkFactory,
    *,
    retry: bool = False,
    summary: str | None = "answer",
    run_status: RunStatus = RunStatus.SUCCEEDED,
    with_event: bool = True,
    with_plan: bool = True,
    plan_status: ScheduleFireDeliveryPlanStatus = ScheduleFireDeliveryPlanStatus.READY,
    reason_code: str | None = None,
    route_max_body_chars: int | None = 16000,
    route_fingerprint: str | None = "a" * 64,
    content_rejected_run_id: RunId | None = None,
    reject_self: bool = False,
) -> tuple[ScheduleFireId, RunId]:
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
        if run_status is RunStatus.SUCCEEDED:
            root.succeed(T0 + timedelta(seconds=1))
        elif run_status is RunStatus.FAILED:
            root.fail(
                T0 + timedelta(seconds=1), Failure("x", "failed", False, FailureCause.RUNTIME)
            )
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
    if with_plan:
        if plan_status is ScheduleFireDeliveryPlanStatus.SUPPRESSED:
            assert reason_code is not None
            plan = ScheduleFireDeliveryPlan.suppressed(
                id=ScheduleFireDeliveryPlanId.new(),
                schedule_fire_id=fire.id,
                schedule_id=fire.schedule_id,
                execution_id=root.id,
                route_id="ops.primary",
                reason_code=reason_code,
                created_at=T0,
            )
        else:
            plan = ScheduleFireDeliveryPlan(
                id=ScheduleFireDeliveryPlanId.new(),
                schedule_fire_id=fire.id,
                schedule_id=fire.schedule_id,
                execution_id=root.id,
                route_id="ops.primary",
                route_fingerprint=route_fingerprint,
                route_max_body_chars=route_max_body_chars,
                content_source=ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1,
                status=ScheduleFireDeliveryPlanStatus.READY,
                reason_code=None,
                content_rejected_run_id=effective.id if reject_self else content_rejected_run_id,
                created_at=T0,
            )
        with factory() as uow:
            uow.schedule_fire_delivery_plans.add_for_fire(plan, fire)
            uow.commit()
    if with_event and summary is not None:
        with factory() as uow:
            LifecycleEvents.append_run_events(
                uow, effective, T0, [(RunEventType.AGENT_FINISHED, {"summary": summary}, None)]
            )
            uow.commit()
    return fire_id, effective.id


def _seed_ready_plan(factory: UnitOfWorkFactory, *, retry: bool) -> tuple[ScheduleFireId, RunId]:
    return _seed_case(factory, retry=retry)


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


class TestDeliveryExactlyOnce:
    def test_two_materializers_race_for_one_ready_plan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_id, run_id = _seed_case(factory, summary="answer")
            scheduler_a = MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10)
            scheduler_b = MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10)
            barrier = threading.Barrier(2)
            original = scheduler_a._materialize_one

            def synchronized(fire: ScheduleFireId) -> bool:
                barrier.wait(timeout=5)
                return original(fire)

            monkeypatch.setattr(scheduler_a, "_materialize_one", synchronized)
            monkeypatch.setattr(scheduler_b, "_materialize_one", synchronized)

            results: list[int] = []
            errors: list[BaseException] = []

            def execute(scheduler: MaterializeScheduledAnswerDeliveries) -> None:
                try:
                    results.append(scheduler.execute())
                except BaseException as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=execute, args=(scheduler_a,)),
                threading.Thread(target=execute, args=(scheduler_b,)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not any(t.is_alive() for t in threads)
            assert errors == []
            assert sorted(results) == [0, 1]

            with engine.connect() as connection:
                count = connection.scalar(select(func.count()).select_from(OutboundDeliveryRow))
            assert count == 1
            with factory() as uow:
                delivery = uow.deliveries.get_by_source_schedule_fire_id(fire_id)
            assert delivery is not None and delivery.source_run_id == run_id
        finally:
            engine.dispose()

    def test_source_schedule_fire_id_unique_is_final_db_fence(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_id, run_id = _seed_case(factory, summary="answer")
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 1
            )

            body = "second"
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            with Session(engine) as session, pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO outbound_deliveries "
                        "(id, source_kind, source_run_id, source_schedule_fire_id, route_id, "
                        "route_fingerprint, body, body_sha256, status, available_at, "
                        "attempt_count, claim_generation, created_at, updated_at) "
                        "VALUES (:id, :sk, :srid, :sfid, :rid, :rfp, :body, :sha, :st, :av, "
                        ":ac, :cg, :ca, :ua)"
                    ),
                    {
                        "id": str(DeliveryId.new()),
                        "sk": "scheduled_run_answer",
                        "srid": str(run_id),
                        "sfid": str(fire_id),
                        "rid": "ops.primary",
                        "rfp": "a" * 64,
                        "body": body,
                        "sha": digest,
                        "st": "queued",
                        "av": T0,
                        "ac": 0,
                        "cg": 0,
                        "ca": T0,
                        "ua": T0,
                    },
                )
                session.flush()

            with engine.connect() as connection:
                count = connection.scalar(select(func.count()).select_from(OutboundDeliveryRow))
            assert count == 1
        finally:
            engine.dispose()


class TestCrashAtomicity:
    def test_crash_before_commit_leaves_zero_partial_delivery_and_retry_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_id, run_id = _seed_case(factory, summary="answer")
            materializer = MaterializeScheduledAnswerDeliveries(
                factory, FixedClock(), batch_size=10
            )

            def crashing_commit(self: object) -> None:
                raise RuntimeError("simulated crash before commit")

            monkeypatch.setattr(uow_mod.SqlAlchemyUnitOfWork, "commit", crashing_commit)

            assert materializer.execute() == 0

            monkeypatch.undo()

            with engine.connect() as connection:
                count = connection.scalar(select(func.count()).select_from(OutboundDeliveryRow))
            assert count == 0
            with factory() as uow:
                plan = uow.schedule_fire_delivery_plans.get_by_fire(fire_id)
            assert plan is not None
            assert plan.content_rejected_run_id is None

            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 1
            )
            with factory() as uow:
                delivery = uow.deliveries.get_by_source_schedule_fire_id(fire_id)
            assert delivery is not None and delivery.source_run_id == run_id
        finally:
            engine.dispose()


class TestCandidateFairness:
    def test_query_only_selects_succeeded_unrejected_ready_plans(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            active_id, _ = _seed_case(factory, run_status=RunStatus.RUNNING, summary="answer")
            failed_id, _ = _seed_case(factory, run_status=RunStatus.FAILED)
            rejected_id, _ = _seed_case(factory, reject_self=True, summary="answer")
            suppressed_id, _ = _seed_case(
                factory,
                plan_status=ScheduleFireDeliveryPlanStatus.SUPPRESSED,
                reason_code="schedule_delivery_route_disabled",
            )
            no_plan_id, _ = _seed_case(factory, with_plan=False, summary="answer")
            valid_id, valid_run_id = _seed_case(factory, summary="answer")

            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 1
            )
            with factory() as uow:
                valid = uow.deliveries.get_by_source_schedule_fire_id(valid_id)
                assert valid is not None and valid.source_run_id == valid_run_id
                assert uow.deliveries.get_by_source_schedule_fire_id(active_id) is None
                assert uow.deliveries.get_by_source_schedule_fire_id(failed_id) is None
                assert uow.deliveries.get_by_source_schedule_fire_id(rejected_id) is None
                assert uow.deliveries.get_by_source_schedule_fire_id(suppressed_id) is None
                assert uow.deliveries.get_by_source_schedule_fire_id(no_plan_id) is None
        finally:
            engine.dispose()

    def test_ineligible_candidate_does_not_starve_later_retry(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            active_id, active_run_id = _seed_case(
                factory, run_status=RunStatus.RUNNING, summary="later answer"
            )
            valid_id, _ = _seed_case(factory, summary="answer")
            materializer = MaterializeScheduledAnswerDeliveries(
                factory, FixedClock(), batch_size=10
            )

            assert materializer.execute() == 1
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(valid_id) is not None
                assert uow.deliveries.get_by_source_schedule_fire_id(active_id) is None

            with factory() as uow:
                run = uow.runs.get(active_run_id)
                assert run is not None
                run.succeed(T0 + timedelta(seconds=1))
                uow.runs.save(run)
                uow.commit()

            assert materializer.execute() == 1
            with factory() as uow:
                delivery = uow.deliveries.get_by_source_schedule_fire_id(active_id)
            assert delivery is not None and delivery.body == "later answer"
        finally:
            engine.dispose()


class TestFailClosed:
    def test_suppressed_plan_materializes_zero(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_id, _ = _seed_case(
                factory,
                plan_status=ScheduleFireDeliveryPlanStatus.SUPPRESSED,
                reason_code="schedule_delivery_route_missing",
                summary="answer",
            )
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 0
            )
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(fire_id) is None
        finally:
            engine.dispose()

    def test_historical_null_route_body_bound_fails_closed(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_id, run_id = _seed_case(factory, route_max_body_chars=None, summary="answer")
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 0
            )
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(fire_id) is None
                plan = uow.schedule_fire_delivery_plans.get_by_fire(fire_id)
            assert plan is not None
            assert plan.content_rejected_run_id == run_id
        finally:
            engine.dispose()


class TestFrozenAuthority:
    def test_frozen_route_body_bound_is_enforced(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_id, run_id = _seed_case(factory, route_max_body_chars=5, summary="answer")
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 0
            )
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(fire_id) is None
                plan = uow.schedule_fire_delivery_plans.get_by_fire(fire_id)
            assert plan is not None
            assert plan.content_rejected_run_id == run_id
        finally:
            engine.dispose()

    def test_route_bound_above_max_body_length_is_still_capped(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            oversized_id, _ = _seed_case(factory, route_max_body_chars=20000, summary="x" * 17000)
            accepted_id, _ = _seed_case(factory, route_max_body_chars=20000, summary="y" * 15000)
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 1
            )
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(oversized_id) is None
                delivery = uow.deliveries.get_by_source_schedule_fire_id(accepted_id)
            assert delivery is not None and delivery.body == "y" * 15000
        finally:
            engine.dispose()

    def test_route_rotation_does_not_change_frozen_authority(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            fire_a, _ = _seed_case(factory, summary="answer a")
            fire_b, _ = _seed_case(factory, summary="answer b", route_fingerprint="b" * 64)
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 2
            )
            with factory() as uow:
                a = uow.deliveries.get_by_source_schedule_fire_id(fire_a)
                b = uow.deliveries.get_by_source_schedule_fire_id(fire_b)
            assert a is not None
            assert a.route_id == "ops.primary" and a.route_fingerprint == "a" * 64
            assert b is not None
            assert b.route_id == "ops.primary" and b.route_fingerprint == "b" * 64
        finally:
            engine.dispose()


class TestCandidateIsolation:
    def test_unsafe_summary_does_not_starve_later_candidate(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            unsafe_id, unsafe_run_id = _seed_case(factory, summary="unsafe\x01answer")
            valid_id, valid_run_id = _seed_case(factory, summary="answer")
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 1
            )
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(unsafe_id) is None
                valid = uow.deliveries.get_by_source_schedule_fire_id(valid_id)
                assert valid is not None and valid.source_run_id == valid_run_id
                assert valid.body == "answer"
                unsafe_plan = uow.schedule_fire_delivery_plans.get_by_fire(unsafe_id)
            assert unsafe_plan is not None
            assert unsafe_plan.content_rejected_run_id == unsafe_run_id
        finally:
            engine.dispose()

    def test_missing_final_agent_event_does_not_block_others(self, tmp_path: Path) -> None:
        engine, factory = _factory(tmp_path)
        try:
            no_event_id, no_event_run_id = _seed_case(factory, with_event=False)
            valid_id, _ = _seed_case(factory, summary="answer")
            assert (
                MaterializeScheduledAnswerDeliveries(factory, FixedClock(), batch_size=10).execute()
                == 1
            )
            with factory() as uow:
                assert uow.deliveries.get_by_source_schedule_fire_id(no_event_id) is None
                assert uow.deliveries.get_by_source_schedule_fire_id(valid_id) is not None
                no_event_plan = uow.schedule_fire_delivery_plans.get_by_fire(no_event_id)
            assert no_event_plan is not None
            assert no_event_plan.content_rejected_run_id == no_event_run_id
        finally:
            engine.dispose()

"""Real SQLite proofs for Phase 19 Step 6: policy TOCTOU, materialization,
occurrence atomicity, composite FK, route rotation, scheduler race, migration."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import friday.infrastructure.persistence.unit_of_work as uow_mod
from friday.application.errors import EntityConflict
from friday.application.materialize_due_schedule import MaterializeDueSchedules
from friday.application.ports import UnitOfWorkFactory
from friday.application.put_schedule_delivery_policy import (
    PutScheduleDeliveryPolicy,
    PutScheduleDeliveryPolicyCommand,
)
from friday.application.schedule_lifecycle import CreateSchedule, CreateScheduleCommand
from friday.domain.delivery_route import DeliveryRouteAuthority
from friday.domain.identifiers import (
    RunId,
    ScheduleFireDeliveryPlanId,
    ScheduleFireId,
    ScheduleId,
    TaskId,
)
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.schedule_delivery_policy import ScheduleDeliveryPolicy
from friday.domain.schedule_fire_delivery_plan import (
    ScheduleFireDeliveryPlan,
    ScheduleFireDeliveryPlanStatus,
)
from friday.domain.task import Task
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import (
    ScheduleDeliveryPolicyRow,
    ScheduleFireDeliveryPlanRow,
    ScheduleFireRow,
    ScheduleRow,
)
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value


class Resolver:
    def __init__(
        self,
        *,
        route_id: str = "ops.primary",
        enabled: bool = True,
        fingerprint: str = "a" * 64,
    ) -> None:
        self._route_id = route_id
        self._enabled = enabled
        self._fingerprint = fingerprint

    def resolve(self, route_id: str) -> DeliveryRouteAuthority | None:
        if route_id != self._route_id:
            return None
        return DeliveryRouteAuthority(self._route_id, self._enabled, self._fingerprint)


class MissingResolver:
    @staticmethod
    def resolve(route_id: str) -> DeliveryRouteAuthority | None:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _migrated_factory(tmp_path: Path) -> tuple[Path, Engine, UnitOfWorkFactory]:
    db_path = tmp_path / "sdp.db"
    command.upgrade(_alembic_config(db_path), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    return db_path, engine, create_unit_of_work_factory(create_session_factory(engine))


def _seed_task_and_schedule(
    factory: UnitOfWorkFactory,
    clock: FixedClock,
    *,
    kind: ScheduleKind = ScheduleKind.ONCE,
    cron: str | None = None,
) -> Schedule:
    task = Task.new(id=TaskId.new(), title="scheduled", description="", created_at=T0)
    with factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    schedule = CreateSchedule(factory, clock).execute(
        CreateScheduleCommand(
            task.id,
            kind,
            run_at=T0 + timedelta(minutes=1) if kind is ScheduleKind.ONCE else None,
            cron=cron,
            timezone="UTC",
        )
    )
    clock.now_value = T0 + timedelta(minutes=1)
    return schedule


def _make_schedule_terminal(
    factory: UnitOfWorkFactory, schedule_id: ScheduleId, *, status: str = "cancelled"
) -> None:
    with factory() as uow:
        schedule = uow.schedules.get(schedule_id)
        assert schedule is not None
        if status == "cancelled":
            schedule.cancel(T0)
        else:
            schedule.complete(T0)
        uow.schedules.save(schedule)
        uow.commit()


def _policy_from_db(engine: Engine, schedule_id: ScheduleId) -> ScheduleDeliveryPolicyRow | None:
    with Session(engine) as session:
        return session.get(ScheduleDeliveryPolicyRow, str(schedule_id))


def _schedule_status_from_db(engine: Engine, schedule_id: ScheduleId) -> str | None:
    with Session(engine) as session:
        row = session.get(ScheduleRow, str(schedule_id))
        return row.status if row else None


# ===========================================================================
# 1. Finding #3 + #5 — first-insert TOCTOU + terminal race proof
# ===========================================================================


class TestPolicyTOCTOUAndTerminalRace:
    def test_create_policy_rejected_when_schedule_already_completed(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)
        _make_schedule_terminal(factory, schedule.id, status="completed")

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert not uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        assert _policy_from_db(engine, schedule.id) is None

    def test_create_policy_rejected_when_schedule_already_cancelled(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)
        _make_schedule_terminal(factory, schedule.id, status="cancelled")

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert not uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        assert _policy_from_db(engine, schedule.id) is None

    def test_create_policy_succeeds_on_nonterminal_schedule(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        row = _policy_from_db(engine, schedule.id)
        assert row is not None
        assert row.route_id == "ops.primary"
        assert row.enabled is True

    def test_create_does_not_mutate_existing_policy_when_schedule_terminal(
        self, tmp_path: Path
    ) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        _make_schedule_terminal(factory, schedule.id)
        fetched_before = _policy_from_db(engine, schedule.id)
        assert fetched_before is not None

        updated = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.secondary", enabled=False, now=T0
        )
        with factory() as uow:
            assert not uow.schedule_delivery_policies.put_for_nonterminal_schedule(updated)
            uow.commit()

        fetched_after = _policy_from_db(engine, schedule.id)
        assert fetched_after is not None
        assert fetched_after.route_id == fetched_before.route_id
        assert fetched_after.enabled == fetched_before.enabled

    def test_put_use_case_rejects_terminal_schedule(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)
        _make_schedule_terminal(factory, schedule.id)

        with pytest.raises(EntityConflict):
            PutScheduleDeliveryPolicy(factory, clock).execute(
                PutScheduleDeliveryPolicyCommand(
                    schedule_id=schedule.id,
                    task_id=schedule.task_id,
                    route_id="ops.primary",
                    enabled=True,
                )
            )

        assert _policy_from_db(engine, schedule.id) is None

    def test_update_policy_retains_pre_race_values(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        _make_schedule_terminal(factory, schedule.id)

        updated = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.secondary", enabled=False, now=T0
        )
        with factory() as uow:
            assert not uow.schedule_delivery_policies.put_for_nonterminal_schedule(updated)
            uow.commit()

        row = _policy_from_db(engine, schedule.id)
        assert row is not None
        assert row.route_id == "ops.primary"
        assert row.enabled is True

    def test_update_policy_succeeds_when_schedule_stays_nonterminal(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        updated = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.secondary", enabled=False, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(updated)
            uow.commit()

        row = _policy_from_db(engine, schedule.id)
        assert row is not None
        assert row.route_id == "ops.secondary"
        assert row.enabled is False

    def test_two_sessions_create_race_only_one_wins(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        results: list[bool] = []
        errors: list[BaseException] = []

        def try_create() -> None:
            try:
                policy = ScheduleDeliveryPolicy.new(
                    schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
                )
                with factory() as uow:
                    results.append(
                        uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
                    )
                    uow.commit()
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=try_create) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert all(results)
        row = _policy_from_db(engine, schedule.id)
        assert row is not None
        assert _schedule_status_from_db(engine, schedule.id) in ("active",)


# ===========================================================================
# 2. Step-6 materialization proofs
# ===========================================================================


class TestMaterializationProofs:
    def test_no_policy_materializes_run_fire_no_plan(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        materializer = MaterializeDueSchedules(factory, clock, batch_size=10)
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            assert uow.runs.get(fire.run_id) is not None
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is None
            reloaded = uow.schedules.get(schedule.id)
            assert reloaded is not None
            assert reloaded.next_fire_at is None or reloaded.next_fire_at > T0

    def test_disabled_policy_materializes_run_fire_no_plan(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=False, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        materializer = MaterializeDueSchedules(factory, clock, batch_size=10)
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            assert uow.runs.get(fire.run_id) is not None
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is None

    def test_ready_route_materializes_run_fire_plan_and_advances(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        resolver = Resolver(fingerprint="a" * 64)
        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver
        )
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            run = uow.runs.get(fire.run_id)
            assert run is not None
            assert run.id == run.execution_id
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is not None
            assert plan.schedule_fire_id == fire.id
            assert plan.schedule_id == fire.schedule_id
            assert plan.execution_id == run.id
            assert plan.status is ScheduleFireDeliveryPlanStatus.READY
            assert plan.route_fingerprint == "a" * 64
            reloaded = uow.schedules.get(schedule.id)
            assert reloaded is not None
            assert reloaded.next_fire_at is None or reloaded.next_fire_at > T0

    def test_missing_route_materializes_suppressed_plan(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=MissingResolver()
        )
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is not None
            assert plan.status is ScheduleFireDeliveryPlanStatus.SUPPRESSED
            assert plan.reason_code == "schedule_delivery_route_missing"

    def test_disabled_route_materializes_suppressed_plan(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        resolver = Resolver(enabled=False, fingerprint="a" * 64)
        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver
        )
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is not None
            assert plan.status is ScheduleFireDeliveryPlanStatus.SUPPRESSED
            assert plan.reason_code == "schedule_delivery_route_disabled"


# ===========================================================================
# 3. Occurrence atomicity — rollback + retry
# ===========================================================================


class TestOccurrenceAtomicity:
    def test_rollback_leaves_no_partial_occurrence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        original_next = schedule.next_fire_at

        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=Resolver()
        )

        def crashing_commit(self: object) -> None:
            raise RuntimeError("simulated crash before commit")

        monkeypatch.setattr(uow_mod.SqlAlchemyUnitOfWork, "commit", crashing_commit)

        assert materializer.execute() == 0

        monkeypatch.undo()

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 0
            reloaded = uow.schedules.get(schedule.id)
            assert reloaded is not None
            assert reloaded.next_fire_at == original_next

        with engine.connect() as connection:
            run_count = connection.scalar(select(func.count()).select_from(text("runs")))
            assert run_count == 0

    def test_healthy_retry_materializes_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=Resolver()
        )

        def crashing_commit(self: object) -> None:
            raise RuntimeError("simulated crash before commit")

        monkeypatch.setattr(uow_mod.SqlAlchemyUnitOfWork, "commit", crashing_commit)

        assert materializer.execute() == 0

        monkeypatch.undo()

        clock.now_value += timedelta(seconds=1)

        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is not None
            assert plan.status is ScheduleFireDeliveryPlanStatus.READY

        with engine.connect() as connection:
            run_count = connection.scalar(select(func.count()).select_from(text("runs")))
            assert run_count == 1
            fire_count = connection.scalar(select(func.count()).select_from(ScheduleFireRow))
            assert fire_count == 1
            plan_count = connection.scalar(
                select(func.count()).select_from(ScheduleFireDeliveryPlanRow)
            )
            assert plan_count == 1


# ===========================================================================
# 4. Composite fire binding — SQLite FK enforcement
# ===========================================================================


class TestCompositeFireBinding:
    def _seed_schedule_and_run(self, factory: UnitOfWorkFactory) -> tuple[str, str, str]:
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)
        materializer = MaterializeDueSchedules(factory, clock, batch_size=10)
        materializer.execute()
        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            return str(fires[0].id), str(schedule.id), str(fires[0].run_id)

    def test_fire_schedule_mismatch_rejected(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        fire_id, sched_id, run_id = self._seed_schedule_and_run(factory)
        other_schedule_id = str(ScheduleId.new())

        with Session(engine) as session, pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO schedule_fire_delivery_plans "
                    "(id, schedule_fire_id, schedule_id, execution_id, route_id, "
                    " content_source, status, created_at) "
                    "VALUES (:id, :fid, :sid, :eid, :rid, :cs, :st, :ca)"
                ),
                {
                    "id": str(ScheduleFireDeliveryPlanId.new()),
                    "fid": fire_id,
                    "sid": other_schedule_id,
                    "eid": run_id,
                    "rid": "ops.primary",
                    "cs": "final_agent_summary_v1",
                    "st": "ready",
                    "ca": T0,
                },
            )
            session.flush()

    def test_fire_run_mismatch_rejected(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        fire_id, sched_id, run_id = self._seed_schedule_and_run(factory)
        other_run_id = str(RunId.new())

        with Session(engine) as session, pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO schedule_fire_delivery_plans "
                    "(id, schedule_fire_id, schedule_id, execution_id, route_id, "
                    " content_source, status, created_at) "
                    "VALUES (:id, :fid, :sid, :eid, :rid, :cs, :st, :ca)"
                ),
                {
                    "id": str(ScheduleFireDeliveryPlanId.new()),
                    "fid": fire_id,
                    "sid": sched_id,
                    "eid": other_run_id,
                    "rid": "ops.primary",
                    "cs": "final_agent_summary_v1",
                    "st": "ready",
                    "ca": T0,
                },
            )
            session.flush()

    def test_nonexistent_fire_rejected(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        fire_id, sched_id, run_id = self._seed_schedule_and_run(factory)
        fake_fire_id = str(ScheduleFireId.new())

        with Session(engine) as session, pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO schedule_fire_delivery_plans "
                    "(id, schedule_fire_id, schedule_id, execution_id, route_id, "
                    " content_source, status, created_at) "
                    "VALUES (:id, :fid, :sid, :eid, :rid, :cs, :st, :ca)"
                ),
                {
                    "id": str(ScheduleFireDeliveryPlanId.new()),
                    "fid": fake_fire_id,
                    "sid": sched_id,
                    "eid": run_id,
                    "rid": "ops.primary",
                    "cs": "final_agent_summary_v1",
                    "st": "ready",
                    "ca": T0,
                },
            )
            session.flush()

    def test_duplicate_plan_for_same_fire_rejected(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)
        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=Resolver()
        )
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is not None
            dup = ScheduleFireDeliveryPlan.ready(
                id=ScheduleFireDeliveryPlanId.new(),
                schedule_fire_id=fire.id,
                schedule_id=fire.schedule_id,
                execution_id=fire.run_id,
                route_id="ops.primary",
                route_fingerprint="b" * 64,
                created_at=T0,
            )
            with pytest.raises(EntityConflict):
                uow.schedule_fire_delivery_plans.add_for_fire(dup, fire)
                uow.commit()


# ===========================================================================
# 5. Route rotation — future-fire only
# ===========================================================================


class TestRouteRotation:
    def test_fingerprint_rotation_is_future_fire_only(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock, kind=ScheduleKind.CRON, cron="* * * * *")

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        resolver_a = Resolver(fingerprint="a" * 64)
        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver_a
        )
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            plan_a = uow.schedule_fire_delivery_plans.get_by_fire(fires[0].id)
            assert plan_a is not None
            assert plan_a.route_fingerprint == "a" * 64
            run = uow.runs.get(fires[0].run_id)
            assert run is not None
            run.start(T0)
            run.succeed(T0)
            uow.runs.save(run)
            uow.commit()

        clock.now_value += timedelta(minutes=1)

        resolver_b = Resolver(fingerprint="b" * 64)
        materializer2 = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver_b
        )
        assert materializer2.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 2
            plan_a_reloaded = uow.schedule_fire_delivery_plans.get_by_fire(fires[0].id)
            assert plan_a_reloaded is not None
            assert plan_a_reloaded.route_fingerprint == "a" * 64
            plan_b = uow.schedule_fire_delivery_plans.get_by_fire(fires[1].id)
            assert plan_b is not None
            assert plan_b.route_fingerprint == "b" * 64

    def test_policy_alias_update_does_not_affect_old_plan(self, tmp_path: Path) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)

        schedule = _seed_task_and_schedule(factory, clock, kind=ScheduleKind.CRON, cron="* * * * *")

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        resolver = Resolver(route_id="ops.primary", fingerprint="a" * 64)
        materializer = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver
        )
        assert materializer.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            plan_a = uow.schedule_fire_delivery_plans.get_by_fire(fires[0].id)
            assert plan_a is not None
            assert plan_a.route_id == "ops.primary"
            run = uow.runs.get(fires[0].run_id)
            assert run is not None
            run.start(T0)
            run.succeed(T0)
            uow.runs.save(run)
            uow.commit()

        updated = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.secondary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(updated)
            uow.commit()

        clock.now_value += timedelta(minutes=1)

        resolver2 = Resolver(route_id="ops.secondary", fingerprint="b" * 64)
        materializer2 = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver2
        )
        assert materializer2.execute() == 1

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 2
            plan_a_reloaded = uow.schedule_fire_delivery_plans.get_by_fire(fires[0].id)
            assert plan_a_reloaded is not None
            assert plan_a_reloaded.route_id == "ops.primary"
            plan_b = uow.schedule_fire_delivery_plans.get_by_fire(fires[1].id)
            assert plan_b is not None
            assert plan_b.route_id == "ops.secondary"


# ===========================================================================
# 6. Real scheduler race with plan
# ===========================================================================


class TestSchedulerRaceWithPlan:
    def test_two_schedulers_race_with_enabled_policy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, engine, factory = _migrated_factory(tmp_path)
        clock = FixedClock(T0)
        schedule = _seed_task_and_schedule(factory, clock)

        policy = ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
        with factory() as uow:
            assert uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy)
            uow.commit()

        resolver = Resolver(fingerprint="a" * 64)
        scheduler_a = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver
        )
        scheduler_b = MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=resolver
        )

        barrier = threading.Barrier(2)
        original = scheduler_a._materialize_one

        def synchronized(schedule_id: ScheduleId) -> bool:
            barrier.wait(timeout=5)
            return original(schedule_id)

        monkeypatch.setattr(scheduler_a, "_materialize_one", synchronized)
        monkeypatch.setattr(scheduler_b, "_materialize_one", synchronized)

        results: list[int] = []
        errors: list[BaseException] = []

        def execute(scheduler: MaterializeDueSchedules) -> None:
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

        with factory() as uow:
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=10)
            assert len(fires) == 1
            fire = fires[0]
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire.id)
            assert plan is not None
            assert plan.status is ScheduleFireDeliveryPlanStatus.READY


# ===========================================================================
# 7. Migration 0015 upgrade / downgrade proof
# ===========================================================================


class TestMigration0015:
    def test_0015_upgrade_does_not_backfill_historical_plans(self, tmp_path: Path) -> None:
        db_path = tmp_path / "migrate.db"
        config = _alembic_config(db_path)
        command.upgrade(config, "0014")

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            with Session(engine) as session:
                session.execute(text("PRAGMA foreign_keys=OFF"))
                session.execute(
                    text(
                        "INSERT INTO tasks (id, title, description, status, created_at) "
                        "VALUES (:id, :t, :d, :s, :ca)"
                    ),
                    {"id": "task-1", "t": "test", "d": "", "s": "active", "ca": T0},
                )
                session.execute(
                    text(
                        "INSERT INTO runs (id, task_id, execution_id, status, created_at) "
                        "VALUES (:id, :tid, :eid, :s, :ca)"
                    ),
                    {"id": "run-1", "tid": "task-1", "eid": "run-1", "s": "queued", "ca": T0},
                )
                session.execute(
                    text(
                        "INSERT INTO schedules (id, task_id, kind, run_at, timezone, status, "
                        "next_fire_at, created_at, updated_at) "
                        "VALUES (:id, :tid, :k, :ra, :tz, :st, :nfa, :ca, :ua)"
                    ),
                    {
                        "id": "sched-1",
                        "tid": "task-1",
                        "k": "once",
                        "ra": T0,
                        "tz": "UTC",
                        "st": "active",
                        "nfa": T0,
                        "ca": T0,
                        "ua": T0,
                    },
                )
                session.execute(
                    text(
                        "INSERT INTO schedule_fires (id, schedule_id, scheduled_for, "
                        "fired_at, run_id) "
                        "VALUES (:id, :sid, :sf, :fa, :rid)"
                    ),
                    {
                        "id": "fire-1",
                        "sid": "sched-1",
                        "sf": T0,
                        "fa": T0,
                        "rid": "run-1",
                    },
                )
                session.execute(
                    text(
                        "INSERT INTO runs (id, task_id, execution_id, status, created_at) "
                        "VALUES (:id, :tid, :eid, :s, :ca)"
                    ),
                    {"id": "run-2", "tid": "task-1", "eid": "run-2", "s": "queued", "ca": T0},
                )
                session.execute(
                    text(
                        "INSERT INTO schedule_fires (id, schedule_id, scheduled_for, "
                        "fired_at, run_id) "
                        "VALUES (:id, :sid, :sf, :fa, :rid)"
                    ),
                    {
                        "id": "fire-2",
                        "sid": "sched-1",
                        "sf": T0 + timedelta(minutes=1),
                        "fa": T0 + timedelta(minutes=1),
                        "rid": "run-2",
                    },
                )
                session.commit()

            with engine.connect() as conn:
                fire_count_before = conn.execute(
                    select(func.count()).select_from(ScheduleFireRow)
                ).scalar()
                assert fire_count_before == 2
        finally:
            engine.dispose()

        command.upgrade(config, "0015")

        engine2 = create_engine(f"sqlite:///{db_path}")
        try:
            inspector = inspect(engine2)
            tables = set(inspector.get_table_names())
            assert "schedule_delivery_policies" in tables
            assert "schedule_fire_delivery_plans" in tables

            with engine2.connect() as conn:
                fire_count = conn.execute(
                    select(func.count()).select_from(ScheduleFireRow)
                ).scalar()
                assert fire_count == 2

                plan_count = conn.execute(
                    select(func.count()).select_from(ScheduleFireDeliveryPlanRow)
                ).scalar()
                assert plan_count == 0

            binding = {
                tuple(c["column_names"]) for c in inspector.get_unique_constraints("schedule_fires")
            }
            assert ("id", "schedule_id", "run_id") in binding

            plan_unique = {
                tuple(c["column_names"])
                for c in inspector.get_unique_constraints("schedule_fire_delivery_plans")
            }
            assert ("schedule_fire_id",) in plan_unique

            plan_checks = {
                check["name"]
                for check in inspector.get_check_constraints("schedule_fire_delivery_plans")
            }
            assert "ck_schedule_fire_delivery_plans_status" in plan_checks
            assert "ck_schedule_fire_delivery_plans_content" in plan_checks
            assert "ck_schedule_fire_delivery_plans_fingerprint" in plan_checks
            assert "ck_schedule_fire_delivery_plans_shape" in plan_checks
            assert "ck_schedule_fire_delivery_plans_route_id" in plan_checks

            policy_checks = {
                check["name"]
                for check in inspector.get_check_constraints("schedule_delivery_policies")
            }
            assert "ck_schedule_delivery_policies_route_id" in policy_checks
            assert "ck_schedule_delivery_policies_enabled" in policy_checks

            fk_plan = {
                tuple(fk["constrained_columns"])
                for fk in inspector.get_foreign_keys("schedule_fire_delivery_plans")
            }
            assert ("schedule_fire_id", "schedule_id", "execution_id") in fk_plan
        finally:
            engine2.dispose()

    def test_downgrade_0015_to_0014_then_re_upgrade(self, tmp_path: Path) -> None:
        db_path = tmp_path / "migrate2.db"
        config = _alembic_config(db_path)
        command.upgrade(config, "0015")

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            inspector = inspect(engine)
            assert "schedule_delivery_policies" in set(inspector.get_table_names())
            assert "schedule_fire_delivery_plans" in set(inspector.get_table_names())
        finally:
            engine.dispose()

        command.downgrade(config, "0014")

        engine2 = create_engine(f"sqlite:///{db_path}")
        try:
            inspector = inspect(engine2)
            tables = set(inspector.get_table_names())
            assert "schedule_delivery_policies" not in tables
            assert "schedule_fire_delivery_plans" not in tables
            binding = {
                tuple(c["column_names"]) for c in inspector.get_unique_constraints("schedule_fires")
            }
            assert ("id", "schedule_id", "run_id") not in binding
        finally:
            engine2.dispose()

        command.upgrade(config, "0015")

        engine3 = create_engine(f"sqlite:///{db_path}")
        try:
            inspector = inspect(engine3)
            tables = set(inspector.get_table_names())
            assert "schedule_delivery_policies" in tables
            assert "schedule_fire_delivery_plans" in tables
            binding = {
                tuple(c["column_names"]) for c in inspector.get_unique_constraints("schedule_fires")
            }
            assert ("id", "schedule_id", "run_id") in binding
        finally:
            engine3.dispose()

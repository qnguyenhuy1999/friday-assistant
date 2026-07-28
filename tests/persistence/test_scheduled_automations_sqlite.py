"""Phase 16 scheduler durability proofs against Alembic-migrated SQLite."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from friday.application.delivery_dispatcher import DeliveryDispatcher
from friday.application.delivery_lifecycle import (
    ClaimNextDelivery,
    PersistDeliveryOutcome,
    VerifyDeliveryClaim,
)
from friday.application.materialize_due_schedule import MaterializeDueSchedules
from friday.application.ports import UnitOfWorkFactory
from friday.application.retry_policy import RetryPolicy
from friday.application.schedule_lifecycle import CreateSchedule, CreateScheduleCommand
from friday.application.worker_coordination import ApplySucceededOutcome, ClaimNextRun
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.scheduled_delivery import ScheduleDeliveryPolicy
from friday.domain.task import Task
from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes
from friday.infrastructure.messaging.webhook_transport import WebhookTransport
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import (
    DeliveryAttemptRow,
    RunWorkItemRow,
    ScheduleFireRow,
)
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def now(self) -> datetime:
        return self.now_value


class _WebhookFixture:
    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.effects: list[bytes] = []

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                fixture.effects.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(fixture.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler


class _WebhookServer:
    def __init__(self, fixture: _WebhookFixture) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        host = cast(str, self._server.server_address[0])
        return f"http://{host}:{self._server.server_address[1]}/webhook"

    def __exit__(self, *args: object) -> None:
        del args
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


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


def test_delivery_policy_and_per_fire_plan_survive_a_session_restart(tmp_path: Path) -> None:
    db_path, first_engine, first_factory = _migrated_stack(tmp_path)
    clock = FixedClock(T0)
    try:
        schedule = _seed_due_schedule(first_factory, clock)
        with first_factory() as uow:
            uow.schedule_delivery_policies.save(
                ScheduleDeliveryPolicy(
                    schedule_id=schedule.id,
                    route_id="personal.notifications",
                    route_fingerprint="a" * 64,
                    enabled=True,
                    created_at=T0,
                    updated_at=T0,
                )
            )
            uow.commit()
        assert MaterializeDueSchedules(first_factory, clock, batch_size=10).execute() == 1
    finally:
        first_engine.dispose()

    restarted_engine = create_engine(f"sqlite:///{db_path}")
    restarted_factory = create_unit_of_work_factory(create_session_factory(restarted_engine))
    try:
        with restarted_factory() as uow:
            policy = uow.schedule_delivery_policies.get(schedule.id)
            fires = uow.schedule_fires.list_for_schedule(schedule.id, limit=1)
            assert policy is not None
            assert len(fires) == 1
            plan = uow.schedule_fire_delivery_plans.get_by_execution(fires[0].run_id)
        assert policy.route_id == "personal.notifications"
        assert policy.created_at.tzinfo is UTC
        assert plan is not None
        assert plan.route_fingerprint == "a" * 64
        assert plan.created_at.tzinfo is UTC
    finally:
        restarted_engine.dispose()


def test_scheduled_answer_reaches_real_webhook_exactly_once(tmp_path: Path) -> None:
    _, engine, factory = _migrated_stack(tmp_path)
    clock = FixedClock(T0)
    fixture = _WebhookFixture()
    try:
        with _WebhookServer(fixture) as endpoint:
            route = MessagingRoute(
                route_id="personal.notifications",
                trusted_description="Test notifications",
                principal_id="test-operator",
                endpoint=endpoint,
                allow_insecure_for_tests=True,
            )
            schedule = _seed_due_schedule(factory, clock)
            with factory() as uow:
                uow.schedule_delivery_policies.save(
                    ScheduleDeliveryPolicy(
                        schedule_id=schedule.id,
                        route_id=route.route_id,
                        route_fingerprint=route.fingerprint,
                        enabled=True,
                        created_at=T0,
                        updated_at=T0,
                    )
                )
                uow.commit()
            assert MaterializeDueSchedules(factory, clock, batch_size=10).execute() == 1
            claim = ClaimNextRun(
                factory,
                clock,
                worker_id="run-worker",
                lease_duration=timedelta(minutes=1),
                candidate_limit=10,
            ).execute()
            assert claim is not None
            ApplySucceededOutcome(factory, clock).execute(
                claim.run_id,
                claim.worker_id,
                claim.claim_token,
                claim.claim_generation,
                ("scheduled result", {"source": "sqlite-e2e"}),
            )

            dispatcher = DeliveryDispatcher(
                claim_next=ClaimNextDelivery(
                    factory,
                    clock,
                    worker_id="delivery-worker",
                    lease_duration=timedelta(minutes=1),
                    candidate_limit=10,
                ),
                verify_claim=VerifyDeliveryClaim(factory, clock),
                persist_outcome=PersistDeliveryOutcome(factory, clock),
                uow_factory=factory,
                clock=clock,
                routes=MessagingRoutes((route,)),
                transport=WebhookTransport(),
                retry_policy=RetryPolicy(5, timedelta(seconds=5), 2, timedelta(minutes=5)),
            )
            assert dispatcher.dispatch_once() is True
            assert dispatcher.dispatch_once() is False
            with factory() as uow:
                delivery = uow.deliveries.list_for_run(claim.run_id)[0]
            assert delivery.status.value == "delivered"
            assert delivery.source_run_id == claim.run_id
            assert fixture.effects == [b'{"text":"scheduled result"}']
            with engine.connect() as connection:
                attempt = connection.execute(
                    select(
                        DeliveryAttemptRow.attempt_number,
                        DeliveryAttemptRow.claim_generation,
                        DeliveryAttemptRow.outcome,
                        DeliveryAttemptRow.failure_code,
                        DeliveryAttemptRow.completed_at,
                    )
                ).one()
            assert attempt.attempt_number == attempt.claim_generation == 1
            assert attempt.outcome == "delivered"
            assert attempt.failure_code is None
            assert attempt.completed_at is not None
    finally:
        engine.dispose()

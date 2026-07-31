"""Required Phase 16 durability proofs for scheduler materialization."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from friday.application.materialize_due_schedule import MaterializeDueSchedules
from friday.application.retry_policy import RetryPolicy
from friday.application.schedule_lifecycle import CreateSchedule, CreateScheduleCommand
from friday.application.schedule_recurrence import coalesced_next, first_occurrence
from friday.application.worker_coordination import ApplyFailedOutcome
from friday.domain.delivery_route import DeliveryRouteAuthority
from friday.domain.errors import DomainValidationError
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import TaskId
from friday.domain.run import RunStatus
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.schedule_delivery_policy import ScheduleDeliveryPolicy
from friday.domain.task import Task
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)
RETRYABLE = Failure("retryable", "retryable", True, FailureCause.RUNTIME, {})


def _prepared(
    now: datetime = T0,
) -> tuple[FakeUnitOfWork, FakeClock, CountingUnitOfWorkFactory, Task]:
    uow = FakeUnitOfWork()
    clock = FakeClock(now)
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="scheduled", description="", created_at=now)
    uow.task_repo.add(task)
    return uow, clock, factory, task


def _schedule(
    factory: CountingUnitOfWorkFactory, clock: FakeClock, task: Task, due: datetime
) -> Schedule:
    return CreateSchedule(factory, clock).execute(
        CreateScheduleCommand(task.id, ScheduleKind.ONCE, run_at=due, timezone="UTC")
    )


def test_two_scheduler_actors_and_restart_materialize_one_durable_fire() -> None:
    uow, clock, factory, task = _prepared()
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    clock.fixed_now = T0 + timedelta(minutes=1)
    first = MaterializeDueSchedules(factory, clock, batch_size=10)
    second = MaterializeDueSchedules(factory, clock, batch_size=10)

    assert first.execute() == 1  # scheduler A / materialized before any execution
    assert second.execute() == 0  # scheduler B or a restarted worker
    assert len(uow.schedule_fire_repo.items) == len(uow.run_repo.items) == 1
    assert uow.schedule_fire_repo.items[0].schedule_id == schedule.id
    assert next(iter(uow.run_repo.items.values())).status is RunStatus.QUEUED


def test_enabled_policy_freezes_ready_plan_without_delivery_content() -> None:
    uow, clock, factory, task = _prepared()
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    uow.schedule_delivery_policy_repo.save(
        ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id,
            route_id="personal.notifications",
            enabled=True,
            now=T0,
        )
    )
    clock.fixed_now = T0 + timedelta(minutes=1)

    class Resolver:
        def resolve(self, route_id: str) -> DeliveryRouteAuthority | None:
            assert route_id == "personal.notifications"
            return DeliveryRouteAuthority(route_id, True, "a" * 64, 16000)

    assert (
        MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=Resolver()
        ).execute()
        == 1
    )
    fire = uow.schedule_fire_repo.items[0]
    plan = uow.schedule_fire_delivery_plan_repo.get_by_fire(fire.id)
    assert plan is not None
    assert plan.route_fingerprint == "a" * 64
    assert plan.route_max_body_chars == 16000
    assert plan.execution_id == fire.run_id


def test_missing_route_suppresses_delivery_without_blocking_run_or_fire() -> None:
    uow, clock, factory, task = _prepared()
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    uow.schedule_delivery_policy_repo.save(
        ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="personal.notifications", enabled=True, now=T0
        )
    )
    clock.fixed_now = T0 + timedelta(minutes=1)

    class MissingResolver:
        def resolve(self, route_id: str) -> DeliveryRouteAuthority | None:
            return None

    assert (
        MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=MissingResolver()
        ).execute()
        == 1
    )
    assert len(uow.run_repo.items) == len(uow.schedule_fire_repo.items) == 1
    plan = next(iter(uow.schedule_fire_delivery_plan_repo.items.values()))
    assert plan.reason_code == "schedule_delivery_route_missing"


def test_one_broken_schedule_does_not_starve_other_due_schedules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("friday.application.materialize_due_schedule")
    monkeypatch.setattr(logger, "disabled", False)
    caplog.set_level(logging.ERROR, logger=logger.name)
    uow, clock, factory, task = _prepared()
    broken = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    healthy = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    clock.fixed_now = T0 + timedelta(minutes=1)
    materializer = MaterializeDueSchedules(factory, clock, batch_size=10)
    real_materialize = materializer._materialize_one

    def materialize_with_broken_record(schedule_id: object) -> bool:
        if schedule_id == broken.id:
            raise RuntimeError("corrupt recurrence payload")
        return real_materialize(schedule_id)  # type: ignore[arg-type]

    monkeypatch.setattr(materializer, "_materialize_one", materialize_with_broken_record)

    assert materializer.execute() == 1
    assert len(uow.schedule_fire_repo.items) == 1
    assert uow.schedule_fire_repo.items[0].schedule_id == healthy.id
    assert "schedule.materialization_failed" in caplog.text


def test_overlap_is_deferred_until_retry_lineage_is_terminal_then_overdue_fires_once() -> None:
    uow, clock, factory, task = _prepared()
    schedule = CreateSchedule(factory, clock).execute(
        CreateScheduleCommand(task.id, ScheduleKind.CRON, cron="* * * * *", timezone="UTC")
    )
    assert schedule.next_fire_at is not None
    clock.fixed_now = schedule.next_fire_at
    materializer = MaterializeDueSchedules(factory, clock, batch_size=10)
    assert materializer.execute() == 1
    root = next(iter(uow.run_repo.items.values()))
    assert uow.work_queue_repo.try_claim(
        root.id, "worker", "token", clock.now(), clock.now() + timedelta(minutes=1)
    )
    root.start(clock.now())
    item = uow.work_queue_repo.get(root.id)
    assert item is not None
    generation = item.claim_generation
    ApplyFailedOutcome(
        factory, clock, retry_policy=RetryPolicy(3, timedelta(1), 2, timedelta(5))
    ).execute(root.id, "worker", "token", generation, RETRYABLE)
    retry = next(run for run in uow.run_repo.items.values() if run.id != root.id)
    clock.fixed_now += timedelta(minutes=1)
    assert materializer.execute() == 0
    assert schedule.next_fire_at is not None
    assert schedule.next_fire_at <= clock.now()  # deferred, never dropped
    retry.start(clock.now())
    retry.succeed(clock.now())
    assert materializer.execute() == 1
    assert len(uow.schedule_fire_repo.items) == 2


def test_manual_execution_history_does_not_consume_new_schedule_retry_budget() -> None:
    uow, clock, factory, task = _prepared()
    # Independent manual executions have distinct roots and must not affect a
    # scheduled occurrence's retry count.
    for _ in range(5):
        from friday.application.commands import StartRunCommand
        from friday.application.start_run import StartRun

        StartRun(factory, clock).execute(StartRunCommand(task.id))
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    assert schedule.next_fire_at is not None
    clock.fixed_now = schedule.next_fire_at
    assert MaterializeDueSchedules(factory, clock, batch_size=10).execute() == 1
    scheduled = list(uow.run_repo.items.values())[-1]
    assert uow.run_repo.count_for_execution(scheduled.execution_id) == 1


def test_past_one_shot_and_dst_gap_are_rejected_and_dst_policy_is_explicit() -> None:
    with pytest.raises(DomainValidationError):
        first_occurrence(ScheduleKind.ONCE, None, T0 - timedelta(seconds=1), "UTC", T0)
    with pytest.raises(DomainValidationError):
        first_occurrence(
            ScheduleKind.ONCE, None, datetime(2026, 3, 8, 2, 30), "America/New_York", T0
        )
    # Fall-back ambiguity is deterministic: fold=0, the earlier instant.
    assert first_occurrence(
        ScheduleKind.ONCE, None, datetime(2026, 11, 1, 1, 30), "America/New_York", T0
    ) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_cron_dst_spring_and_fall_back_behavior_is_pinned() -> None:
    # croniter advances a nonexistent 02:30 spring wall time to 03:00 EDT.
    assert first_occurrence(
        ScheduleKind.CRON,
        "30 2 * * *",
        None,
        "America/New_York",
        datetime(2026, 3, 8, 6, 59, tzinfo=UTC),
    ) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    # During the repeated fall-back hour, both distinct 01:30 instants occur.
    assert first_occurrence(
        ScheduleKind.CRON,
        "30 1 * * *",
        None,
        "America/New_York",
        datetime(2026, 11, 1, 4, 59, tzinfo=UTC),
    ) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert first_occurrence(
        ScheduleKind.CRON,
        "30 1 * * *",
        None,
        "America/New_York",
        datetime(2026, 11, 1, 5, 31, tzinfo=UTC),
    ) == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_downtime_coalescing_jumps_directly_to_the_next_future_cron_occurrence() -> None:
    uow, clock, factory, task = _prepared()
    schedule = CreateSchedule(factory, clock).execute(
        CreateScheduleCommand(task.id, ScheduleKind.CRON, cron="* * * * *", timezone="UTC")
    )
    now = T0 + timedelta(days=180)
    expected = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    assert coalesced_next(schedule, fired_at=T0, now=now) == expected


def test_disabled_policy_produces_no_delivery_plan() -> None:
    uow, clock, factory, task = _prepared()
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    uow.schedule_delivery_policy_repo.save(
        ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=False, now=T0
        )
    )
    clock.fixed_now = T0 + timedelta(minutes=1)

    class Resolver:
        @staticmethod
        def resolve(route_id: str) -> DeliveryRouteAuthority | None:
            return DeliveryRouteAuthority(route_id, True, "a" * 64, 16000)

    assert (
        MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=Resolver()
        ).execute()
        == 1
    )
    assert len(uow.schedule_fire_repo.items) == 1
    assert len(uow.schedule_fire_delivery_plan_repo.items) == 0


def test_disabled_route_suppresses_plan_with_disabled_reason() -> None:
    uow, clock, factory, task = _prepared()
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    uow.schedule_delivery_policy_repo.save(
        ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
    )
    clock.fixed_now = T0 + timedelta(minutes=1)

    class DisabledResolver:
        @staticmethod
        def resolve(route_id: str) -> DeliveryRouteAuthority | None:
            return DeliveryRouteAuthority(route_id, False, "a" * 64, 16000)

    assert (
        MaterializeDueSchedules(
            factory, clock, batch_size=10, delivery_route_authority_resolver=DisabledResolver()
        ).execute()
        == 1
    )
    plan = next(iter(uow.schedule_fire_delivery_plan_repo.items.values()))
    assert plan.reason_code == "schedule_delivery_route_disabled"


def test_mismatched_resolver_alias_never_ready() -> None:
    uow, clock, factory, task = _prepared()
    schedule = _schedule(factory, clock, task, T0 + timedelta(minutes=1))
    uow.schedule_delivery_policy_repo.save(
        ScheduleDeliveryPolicy.new(
            schedule_id=schedule.id, route_id="ops.primary", enabled=True, now=T0
        )
    )
    clock.fixed_now = T0 + timedelta(minutes=1)

    class WrongAliasResolver:
        @staticmethod
        def resolve(route_id: str) -> DeliveryRouteAuthority | None:
            assert route_id == "ops.primary"
            return DeliveryRouteAuthority("ops.secondary", True, "a" * 64, 16000)

    assert (
        MaterializeDueSchedules(
            factory,
            clock,
            batch_size=10,
            delivery_route_authority_resolver=WrongAliasResolver(),
        ).execute()
        == 1
    )
    plan = next(iter(uow.schedule_fire_delivery_plan_repo.items.values()))
    assert plan.reason_code == "schedule_delivery_route_missing"

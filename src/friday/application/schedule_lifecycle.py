"""Schedule create/control/read use cases. They persist timing metadata only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.application.errors import EntityConflict, ScheduleNotFound, TaskNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.schedule_recurrence import (
    _resolve_run_at,
    first_occurrence,
    next_occurrence,
)
from friday.domain.errors import InvalidStateTransition
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.schedule import Schedule, ScheduleKind, validate_timezone
from friday.domain.scheduled_delivery import ScheduleDeliveryPolicy


@dataclass(frozen=True, slots=True)
class CreateScheduleCommand:
    task_id: TaskId
    kind: ScheduleKind
    cron: str | None = None
    run_at: datetime | None = None
    timezone: str = "UTC"
    delivery_route_id: str | None = None
    delivery_route_fingerprint: str | None = None


class CreateSchedule:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, command: CreateScheduleCommand) -> Schedule:
        with self._uow_factory() as uow:
            task = uow.tasks.get(command.task_id)
            if task is None:
                raise TaskNotFound(command.task_id)
            now = self._clock.now()
            validate_timezone(command.timezone)
            run_at = (
                _resolve_run_at(command.run_at, command.timezone)
                if command.kind is ScheduleKind.ONCE and command.run_at is not None
                else command.run_at
            )
            schedule = Schedule.new(
                id=ScheduleId.new(),
                task_id=task.id,
                kind=command.kind,
                cron=command.cron,
                run_at=run_at,
                timezone=command.timezone,
                now=now,
                next_fire_at=first_occurrence(
                    command.kind, command.cron, run_at, command.timezone, now
                ),
            )
            uow.schedules.add(schedule)
            if (command.delivery_route_id is None) != (command.delivery_route_fingerprint is None):
                raise ValueError(
                    "schedule delivery route and fingerprint must be provided together"
                )
            if command.delivery_route_id is not None:
                assert command.delivery_route_fingerprint is not None
                uow.schedule_delivery_policies.save(
                    ScheduleDeliveryPolicy(
                        schedule_id=schedule.id,
                        route_id=command.delivery_route_id,
                        route_fingerprint=command.delivery_route_fingerprint,
                        enabled=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            uow.commit()
            return schedule


class ScheduleControl:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def _change(
        self, schedule_id: ScheduleId, action: str, *, task_id: TaskId | None = None
    ) -> Schedule:
        with self._uow_factory() as uow:
            schedule = uow.schedules.get(schedule_id)
            if schedule is None:
                raise ScheduleNotFound(schedule_id)
            if task_id is not None and schedule.task_id != task_id:
                raise ScheduleNotFound(schedule_id)
            now = self._clock.now()
            try:
                if action == "resume":
                    schedule.resume(now, next_occurrence(schedule, now))
                else:
                    getattr(schedule, action)(now)
            except InvalidStateTransition as exc:
                raise EntityConflict(str(exc)) from exc
            uow.schedules.save(schedule)
            uow.commit()
            return schedule

    def pause(self, schedule_id: ScheduleId, *, task_id: TaskId | None = None) -> Schedule:
        return self._change(schedule_id, "pause", task_id=task_id)

    def resume(self, schedule_id: ScheduleId, *, task_id: TaskId | None = None) -> Schedule:
        return self._change(schedule_id, "resume", task_id=task_id)

    def cancel(self, schedule_id: ScheduleId, *, task_id: TaskId | None = None) -> Schedule:
        return self._change(schedule_id, "cancel", task_id=task_id)


class ConfigureScheduleDelivery:
    """Change standing authority for future fires only.

    Existing ScheduleFireDeliveryPlan rows are immutable snapshots, so this
    policy update cannot retarget an occurrence already materialized.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(
        self,
        schedule_id: ScheduleId,
        *,
        task_id: TaskId,
        route_id: str | None,
        route_fingerprint: str | None,
    ) -> Schedule:
        if (route_id is None) != (route_fingerprint is None):
            raise ValueError("schedule delivery route and fingerprint must be provided together")
        with self._uow_factory() as uow:
            schedule = uow.schedules.get(schedule_id)
            if schedule is None or schedule.task_id != task_id:
                raise ScheduleNotFound(schedule_id)
            if route_id is None:
                uow.schedule_delivery_policies.delete(schedule_id)
            else:
                assert route_fingerprint is not None
                now = self._clock.now()
                existing = uow.schedule_delivery_policies.get(schedule_id)
                uow.schedule_delivery_policies.save(
                    ScheduleDeliveryPolicy(
                        schedule_id=schedule_id,
                        route_id=route_id,
                        route_fingerprint=route_fingerprint,
                        enabled=True,
                        created_at=existing.created_at if existing is not None else now,
                        updated_at=now,
                    )
                )
            uow.commit()
            return schedule


class GetSchedule:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, schedule_id: ScheduleId, *, task_id: TaskId | None = None) -> Schedule:
        with self._uow_factory() as uow:
            result = uow.schedules.get(schedule_id)
            if result is None or (task_id is not None and result.task_id != task_id):
                raise ScheduleNotFound(schedule_id)
            return result

    def list_for_task(self, task_id: TaskId, limit: int = 100) -> list[Schedule]:
        with self._uow_factory() as uow:
            return uow.schedules.list_for_task(task_id, limit)

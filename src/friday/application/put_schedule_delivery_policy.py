"""Single transactional authority transition for a schedule delivery policy."""

from __future__ import annotations

from dataclasses import dataclass

from friday.application.errors import EntityConflict, ScheduleNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.schedule import TERMINAL_SCHEDULE_STATUSES
from friday.domain.schedule_delivery_policy import ScheduleDeliveryPolicy


@dataclass(frozen=True, slots=True)
class PutScheduleDeliveryPolicyCommand:
    schedule_id: ScheduleId
    task_id: TaskId
    route_id: str
    enabled: bool


class PutScheduleDeliveryPolicy:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    def execute(self, command: PutScheduleDeliveryPolicyCommand) -> ScheduleDeliveryPolicy:
        with self._uow_factory() as uow:
            schedule = uow.schedules.get(command.schedule_id)
            if schedule is None or schedule.task_id != command.task_id:
                raise ScheduleNotFound(command.schedule_id)
            if schedule.status in TERMINAL_SCHEDULE_STATUSES:
                raise EntityConflict("terminal schedule cannot own delivery policy")
            now = self._clock.now()
            policy = uow.schedule_delivery_policies.get_for_schedule(schedule.id)
            if policy is None:
                policy = ScheduleDeliveryPolicy.new(
                    schedule_id=schedule.id,
                    route_id=command.route_id,
                    enabled=command.enabled,
                    now=now,
                )
            else:
                policy.update_route(command.route_id, now)
                policy.enable(now) if command.enabled else policy.disable(now)
            if not uow.schedule_delivery_policies.put_for_nonterminal_schedule(policy):
                raise EntityConflict("schedule became terminal while updating delivery policy")
            uow.commit()
            return policy

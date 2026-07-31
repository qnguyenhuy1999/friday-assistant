"""Atomic, idempotent ScheduleFire -> queued Run materialization."""

from __future__ import annotations

import logging

from friday.application.errors import EntityConflict, TaskNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.schedule_recurrence import coalesced_next
from friday.application.start_run import StartRun
from friday.domain.delivery_route import DeliveryRouteAuthorityResolver
from friday.domain.identifiers import ScheduleFireDeliveryPlanId, ScheduleFireId, ScheduleId
from friday.domain.schedule import ScheduleStatus
from friday.domain.schedule_fire import ScheduleFire
from friday.domain.schedule_fire_delivery_plan import ScheduleFireDeliveryPlan

logger = logging.getLogger(__name__)


class MaterializeDueSchedules:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        batch_size: int,
        delivery_route_authority_resolver: DeliveryRouteAuthorityResolver | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size
        self._delivery_route_authority_resolver = delivery_route_authority_resolver

    def execute(self) -> int:
        with self._uow_factory() as uow:
            ids = [x.id for x in uow.schedules.list_due(self._clock.now(), self._batch_size)]
        materialized = 0
        for schedule_id in ids:
            try:
                materialized += self._materialize_one(schedule_id)
            except Exception:  # noqa: BLE001 - one corrupt schedule must not starve its batch
                # The scheduler is a best-effort batch dispatcher.  Keep the
                # durable row due for a later retry, but always let unrelated
                # schedules in this batch make progress.
                logger.exception(
                    "schedule.materialization_failed", extra={"schedule_id": str(schedule_id)}
                )
        return materialized

    def _materialize_one(self, schedule_id: ScheduleId) -> bool:
        try:
            with self._uow_factory() as uow:
                schedule = uow.schedules.get(schedule_id)
                now = self._clock.now()
                if (
                    schedule is None
                    or schedule.status is not ScheduleStatus.ACTIVE
                    or schedule.next_fire_at is None
                    or schedule.next_fire_at > now
                ):
                    return False
                task = uow.tasks.get(schedule.task_id)
                if task is None:
                    raise TaskNotFound(schedule.task_id)
                if task.status.value not in ("pending", "active"):
                    schedule.complete(now)
                    uow.schedules.save(schedule)
                    uow.commit()
                    return False
                if uow.schedule_fires.has_non_terminal_execution_for_schedule(schedule.id):
                    # Keep the due occurrence durable.  Once the prior
                    # execution (including retries) becomes terminal, this
                    # exact overdue occurrence is materialized once.
                    uow.commit()
                    return False
                occurrence = schedule.next_fire_at
                result = StartRun.execute_in_uow(uow, task, now)
                root_run = uow.runs.get(result.run_id)
                if root_run is None or root_run.id != root_run.execution_id:
                    raise EntityConflict("scheduled fire requires a root execution run")
                fire = ScheduleFire.new(
                    id=ScheduleFireId.new(),
                    schedule_id=schedule.id,
                    scheduled_for=occurrence,
                    fired_at=now,
                    run_id=result.run_id,
                )
                uow.schedule_fires.add(fire)
                policy = uow.schedule_delivery_policies.get_for_schedule(schedule.id)
                if policy is not None and policy.enabled:
                    authority = (
                        self._delivery_route_authority_resolver.resolve(policy.route_id)
                        if self._delivery_route_authority_resolver
                        else None
                    )
                    if authority is None:
                        plan = ScheduleFireDeliveryPlan.suppressed(
                            id=ScheduleFireDeliveryPlanId.new(),
                            schedule_fire_id=fire.id,
                            schedule_id=schedule.id,
                            execution_id=result.run_id,
                            route_id=policy.route_id,
                            reason_code="schedule_delivery_route_missing",
                            created_at=now,
                        )
                    elif authority.route_id != policy.route_id:
                        # A resolver must not accidentally lend authority for
                        # alias Y to a policy that requested alias X.
                        plan = ScheduleFireDeliveryPlan.suppressed(
                            id=ScheduleFireDeliveryPlanId.new(),
                            schedule_fire_id=fire.id,
                            schedule_id=schedule.id,
                            execution_id=result.run_id,
                            route_id=policy.route_id,
                            reason_code="schedule_delivery_route_missing",
                            created_at=now,
                        )
                    elif not authority.enabled:
                        plan = ScheduleFireDeliveryPlan.suppressed(
                            id=ScheduleFireDeliveryPlanId.new(),
                            schedule_fire_id=fire.id,
                            schedule_id=schedule.id,
                            execution_id=result.run_id,
                            route_id=policy.route_id,
                            reason_code="schedule_delivery_route_disabled",
                            created_at=now,
                        )
                    else:
                        plan = ScheduleFireDeliveryPlan.ready(
                            id=ScheduleFireDeliveryPlanId.new(),
                            schedule_fire_id=fire.id,
                            schedule_id=schedule.id,
                            execution_id=result.run_id,
                            route_id=policy.route_id,
                            route_fingerprint=authority.fingerprint,
                            route_max_body_chars=authority.max_body_chars,
                            created_at=now,
                        )
                    uow.schedule_fire_delivery_plans.add_for_fire(plan, fire)
                schedule.advance_after_fire(
                    now=now,
                    next_fire_at=coalesced_next(schedule, fired_at=occurrence, now=now),
                )
                uow.schedules.save(schedule)
                uow.commit()
                return True
        except EntityConflict:
            # The unique(schedule_id, scheduled_for) fence means another
            # scheduler won this occurrence. It is an idempotent no-op.
            return False

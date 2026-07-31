"""Bounded maintenance ticks for worker claims and approval deadlines."""

from __future__ import annotations

import logging

from friday.application.approval_workflow import ExpireApproval
from friday.application.commands import ExpireApprovalCommand
from friday.application.errors import EntityConflict
from friday.application.list_events import canonical_final_agent_summary
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.results import ApprovalRequestResult
from friday.domain.event import RunEventType
from friday.domain.identifiers import DeliveryId, RunId, ScheduleFireId
from friday.domain.outbound_delivery import MAX_BODY_LENGTH, DeliverySourceKind, OutboundDelivery
from friday.domain.run import TERMINAL_RUN_STATUSES, RunStatus
from friday.domain.schedule_fire_delivery_plan import (
    ScheduleFireDeliveryContentSource,
    ScheduleFireDeliveryPlanStatus,
)

logger = logging.getLogger(__name__)


class ScheduledAnswerContentGate:
    """Deterministic, Friday-owned validation for canonical agent summaries."""

    def validate(self, summary: str | None, max_body_chars: int | None) -> str | None:
        if not isinstance(summary, str):
            return None
        normalized = summary.replace("\r\n", "\n").replace("\r", "\n").strip()
        if (
            not normalized
            or not isinstance(max_body_chars, int)
            or isinstance(max_body_chars, bool)
            or max_body_chars <= 0
            or len(normalized) > min(max_body_chars, MAX_BODY_LENGTH)
        ):
            return None
        if any(ord(char) < 32 and char != "\n" for char in normalized):
            return None
        return normalized


class MaterializeScheduledAnswerDeliveries:
    """Create at most one durable delivery intent per ready schedule fire.

    This use case owns no route resolution, transport, model, or credentials.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size
        self._gate = ScheduledAnswerContentGate()

    def execute(self) -> int:
        with self._uow_factory() as uow:
            fire_ids = uow.schedule_fire_delivery_plans.list_ready_without_delivery(
                self._batch_size
            )
        materialized = 0
        for fire_id in fire_ids:
            try:
                materialized += int(self._materialize_one(fire_id))
            except EntityConflict:
                # The unique source_schedule_fire_id constraint is the final
                # race fence.  A loser has achieved the idempotent outcome.
                continue
            except Exception:  # noqa: BLE001 - candidate faults are isolated
                logger.warning(
                    "scheduler.answer_materialization_candidate_failed",
                    extra={"schedule_fire_id": str(fire_id), "reason_code": "unexpected_error"},
                )
                continue
        return materialized

    def _materialize_one(self, fire_id: ScheduleFireId) -> bool:
        with self._uow_factory() as uow:
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire_id)
            if plan is None or plan.status is not ScheduleFireDeliveryPlanStatus.READY:
                return False
            if uow.deliveries.get_by_source_schedule_fire_id(plan.schedule_fire_id) is not None:
                return False
            run = uow.runs.get_latest_for_execution(plan.execution_id)
            if run is None or run.execution_id != plan.execution_id:
                return False
            if run.status is not RunStatus.SUCCEEDED:
                return False
            if plan.content_source is not ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1:
                return self._reject_content(uow, plan.schedule_fire_id, run.id)
            event = uow.events.latest_of_type_for_run(run.id, RunEventType.AGENT_FINISHED)
            body = self._gate.validate(
                canonical_final_agent_summary(event), plan.route_max_body_chars
            )
            if body is None or plan.route_fingerprint is None:
                return self._reject_content(uow, plan.schedule_fire_id, run.id)
            now = self._clock.now()
            uow.deliveries.add(
                OutboundDelivery.new(
                    id=DeliveryId.new(),
                    source_kind=DeliverySourceKind.SCHEDULED_RUN_ANSWER,
                    source_run_id=run.id,
                    source_tool_invocation_id=None,
                    source_schedule_fire_id=plan.schedule_fire_id,
                    route_id=plan.route_id,
                    route_fingerprint=plan.route_fingerprint,
                    subject=None,
                    body=body,
                    available_at=now,
                    created_at=now,
                )
            )
            uow.commit()
            return True

    @staticmethod
    def _reject_content(uow: UnitOfWork, fire_id: ScheduleFireId, run_id: RunId) -> bool:
        # A rejection belongs to this effective run only. A later retry in the
        # same execution lineage has a new id and is eligible again.
        if uow.schedule_fire_delivery_plans.mark_content_rejected(fire_id, run_id):
            uow.commit()
        return False


class RecoverExpiredLeases:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size

    def execute(self) -> int:
        with self._uow_factory() as uow:
            now = self._clock.now()
            expired = uow.work_queue.find_expired_claims(now, self._batch_size)
            recovered = 0
            for item in expired:
                run = uow.runs.get(item.run_id)
                if (
                    run is None
                    or run.status in TERMINAL_RUN_STATUSES
                    or run.status is RunStatus.WAITING_FOR_APPROVAL
                ):
                    recovered += int(uow.work_queue.remove_if_lease_expired(item.run_id, now))
                else:
                    recovered += int(uow.work_queue.clear_expired_claim(item.run_id, now))
            uow.commit()
            return recovered


class ExpireDueApprovals:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size

    def execute(self) -> list[ApprovalRequestResult]:
        with self._uow_factory() as uow:
            now = self._clock.now()
            due = uow.approvals.list_due_for_expiry(now, self._batch_size)
            uow.commit()

        expire = ExpireApproval(self._uow_factory, self._clock)
        results: list[ApprovalRequestResult] = []
        for approval in due:
            try:
                results.append(expire.execute(ExpireApprovalCommand(approval.id)))
            except EntityConflict:
                continue
        return results

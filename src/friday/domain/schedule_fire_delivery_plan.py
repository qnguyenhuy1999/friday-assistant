from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.delivery_route import validate_route_id
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunId, ScheduleFireDeliveryPlanId, ScheduleFireId, ScheduleId
from friday.domain.time import ensure_utc


class ScheduleFireDeliveryPlanStatus(StrEnum):
    READY = "ready"
    SUPPRESSED = "suppressed"


class ScheduleFireDeliveryContentSource(StrEnum):
    FINAL_AGENT_SUMMARY_V1 = "final_agent_summary_v1"


_REASONS = frozenset({"schedule_delivery_route_missing", "schedule_delivery_route_disabled"})


@dataclass(frozen=True, slots=True)
class ScheduleFireDeliveryPlan:
    id: ScheduleFireDeliveryPlanId
    schedule_fire_id: ScheduleFireId
    schedule_id: ScheduleId
    execution_id: RunId
    route_id: str
    route_fingerprint: str | None
    content_source: ScheduleFireDeliveryContentSource
    status: ScheduleFireDeliveryPlanStatus
    reason_code: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "route_id", validate_route_id(self.route_id))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if not isinstance(self.content_source, ScheduleFireDeliveryContentSource) or (
            self.content_source is not ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1
        ):
            raise DomainValidationError("ScheduleFireDeliveryPlan.content_source is invalid")
        if not isinstance(self.status, ScheduleFireDeliveryPlanStatus):
            raise DomainValidationError("ScheduleFireDeliveryPlan.status is invalid")
        ready = self.status is ScheduleFireDeliveryPlanStatus.READY
        if ready and (
            not isinstance(self.route_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.route_fingerprint)
            or self.reason_code is not None
        ):
            raise DomainValidationError("ready delivery plan requires fingerprint and no reason")
        if self.status is ScheduleFireDeliveryPlanStatus.SUPPRESSED and (
            self.route_fingerprint is not None or self.reason_code not in _REASONS
        ):
            raise DomainValidationError(
                "suppressed delivery plan requires a stable reason and no fingerprint"
            )

    @classmethod
    def ready(
        cls,
        *,
        id: ScheduleFireDeliveryPlanId,
        schedule_fire_id: ScheduleFireId,
        schedule_id: ScheduleId,
        execution_id: RunId,
        route_id: str,
        route_fingerprint: str,
        created_at: datetime,
    ) -> ScheduleFireDeliveryPlan:
        return cls(
            id,
            schedule_fire_id,
            schedule_id,
            execution_id,
            route_id,
            route_fingerprint,
            ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1,
            ScheduleFireDeliveryPlanStatus.READY,
            None,
            created_at,
        )

    @classmethod
    def suppressed(
        cls,
        *,
        id: ScheduleFireDeliveryPlanId,
        schedule_fire_id: ScheduleFireId,
        schedule_id: ScheduleId,
        execution_id: RunId,
        route_id: str,
        reason_code: str,
        created_at: datetime,
    ) -> ScheduleFireDeliveryPlan:
        return cls(
            id,
            schedule_fire_id,
            schedule_id,
            execution_id,
            route_id,
            None,
            ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1,
            ScheduleFireDeliveryPlanStatus.SUPPRESSED,
            reason_code,
            created_at,
        )

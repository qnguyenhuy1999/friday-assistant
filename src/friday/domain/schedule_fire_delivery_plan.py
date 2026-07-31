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
    route_max_body_chars: int | None
    content_source: ScheduleFireDeliveryContentSource
    status: ScheduleFireDeliveryPlanStatus
    reason_code: str | None
    content_rejected_run_id: RunId | None
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
        if self.content_rejected_run_id is not None and not isinstance(
            self.content_rejected_run_id, RunId
        ):
            raise DomainValidationError("delivery plan rejected run is invalid")
        ready = self.status is ScheduleFireDeliveryPlanStatus.READY
        if ready and (
            not isinstance(self.route_fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.route_fingerprint)
            or (
                self.route_max_body_chars is not None
                and (
                    not isinstance(self.route_max_body_chars, int)
                    or isinstance(self.route_max_body_chars, bool)
                    or self.route_max_body_chars <= 0
                )
            )
            or self.reason_code is not None
        ):
            raise DomainValidationError("ready delivery plan requires fingerprint and no reason")
        if self.status is ScheduleFireDeliveryPlanStatus.SUPPRESSED and (
            self.route_fingerprint is not None
            or self.route_max_body_chars is not None
            or self.reason_code not in _REASONS
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
        route_max_body_chars: int,
        created_at: datetime,
    ) -> ScheduleFireDeliveryPlan:
        if (
            not isinstance(route_max_body_chars, int)
            or isinstance(route_max_body_chars, bool)
            or route_max_body_chars <= 0
        ):
            raise DomainValidationError("ready delivery plan requires a route body bound")
        return cls(
            id,
            schedule_fire_id,
            schedule_id,
            execution_id,
            route_id,
            route_fingerprint,
            route_max_body_chars,
            ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1,
            ScheduleFireDeliveryPlanStatus.READY,
            None,
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
            None,
            ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1,
            ScheduleFireDeliveryPlanStatus.SUPPRESSED,
            reason_code,
            None,
            created_at,
        )

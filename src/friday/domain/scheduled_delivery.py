"""Frozen standing authority for a schedule's final answer delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunId, ScheduleFireId, ScheduleId
from friday.domain.time import ensure_utc

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _fingerprint(value: str) -> str:
    if not _SHA256.fullmatch(value):
        raise DomainValidationError("scheduled delivery route fingerprint must be SHA-256 hex")
    return value


@dataclass(frozen=True, slots=True)
class ScheduleDeliveryPolicy:
    schedule_id: ScheduleId
    route_id: str
    route_fingerprint: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise DomainValidationError("ScheduleDeliveryPolicy.route_id must not be empty")
        _fingerprint(self.route_fingerprint)
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))


@dataclass(frozen=True, slots=True)
class ScheduleFireDeliveryPlan:
    schedule_fire_id: ScheduleFireId
    execution_id: RunId
    route_id: str
    route_fingerprint: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.route_id.strip():
            raise DomainValidationError("ScheduleFireDeliveryPlan.route_id must not be empty")
        _fingerprint(self.route_fingerprint)
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

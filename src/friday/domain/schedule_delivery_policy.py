from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.delivery_route import validate_route_id
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import ScheduleId
from friday.domain.time import ensure_utc


@dataclass(slots=True)
class ScheduleDeliveryPolicy:
    _schedule_id: ScheduleId
    _route_id: str
    _enabled: bool
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def new(
        cls, *, schedule_id: ScheduleId, route_id: str, enabled: bool, now: datetime
    ) -> ScheduleDeliveryPolicy:
        now = ensure_utc(now)
        if not isinstance(enabled, bool):
            raise DomainValidationError("ScheduleDeliveryPolicy.enabled must be boolean")
        return cls(schedule_id, validate_route_id(route_id), enabled, now, now)

    @property
    def schedule_id(self) -> ScheduleId:
        return self._schedule_id

    @property
    def route_id(self) -> str:
        return self._route_id

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def update_route(self, route_id: str, at: datetime) -> None:
        self._route_id, self._updated_at = validate_route_id(route_id), ensure_utc(at)

    def enable(self, at: datetime) -> None:
        self._enabled, self._updated_at = True, ensure_utc(at)

    def disable(self, at: datetime) -> None:
        self._enabled, self._updated_at = False, ensure_utc(at)

from __future__ import annotations

from dataclasses import dataclass, field
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
    _frozen: bool = field(init=False, default=False, repr=False)

    _GUARDED_FIELDS = frozenset(
        {"_schedule_id", "_route_id", "_enabled", "_created_at", "_updated_at"}
    )

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_frozen", False) and name in self._GUARDED_FIELDS:
            raise AttributeError(
                f"ScheduleDeliveryPolicy.{name.removeprefix('_')} changes only through "
                "its lifecycle"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        route_id = validate_route_id(self._route_id)
        if not isinstance(self._enabled, bool):
            raise DomainValidationError("ScheduleDeliveryPolicy.enabled must be boolean")
        created_at = ensure_utc(self._created_at)
        updated_at = ensure_utc(self._updated_at)
        if updated_at < created_at:
            raise DomainValidationError(
                "ScheduleDeliveryPolicy.updated_at must not precede created_at"
            )
        object.__setattr__(self, "_route_id", route_id)
        object.__setattr__(self, "_created_at", created_at)
        object.__setattr__(self, "_updated_at", updated_at)
        object.__setattr__(self, "_frozen", True)

    @classmethod
    def new(
        cls, *, schedule_id: ScheduleId, route_id: str, enabled: bool, now: datetime
    ) -> ScheduleDeliveryPolicy:
        now = ensure_utc(now)
        return cls(schedule_id, route_id, enabled, now, now)

    @classmethod
    def reconstruct(
        cls,
        *,
        schedule_id: ScheduleId,
        route_id: str,
        enabled: bool,
        created_at: datetime,
        updated_at: datetime,
    ) -> ScheduleDeliveryPolicy:
        """Validated persistence reconstruction; never bypass aggregate fences."""
        return cls(schedule_id, route_id, enabled, created_at, updated_at)

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
        route, updated = validate_route_id(route_id), ensure_utc(at)
        if updated < self.created_at:
            raise DomainValidationError(
                "ScheduleDeliveryPolicy.updated_at must not precede created_at"
            )
        object.__setattr__(self, "_route_id", route)
        object.__setattr__(self, "_updated_at", updated)

    def enable(self, at: datetime) -> None:
        updated = ensure_utc(at)
        if updated < self.created_at:
            raise DomainValidationError(
                "ScheduleDeliveryPolicy.updated_at must not precede created_at"
            )
        object.__setattr__(self, "_enabled", True)
        object.__setattr__(self, "_updated_at", updated)

    def disable(self, at: datetime) -> None:
        updated = ensure_utc(at)
        if updated < self.created_at:
            raise DomainValidationError(
                "ScheduleDeliveryPolicy.updated_at must not precede created_at"
            )
        object.__setattr__(self, "_enabled", False)
        object.__setattr__(self, "_updated_at", updated)

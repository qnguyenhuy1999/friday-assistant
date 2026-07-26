"""Durable schedule state. Recurrence computation belongs to application code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.time import ensure_utc


class ScheduleKind(StrEnum):
    ONCE = "once"
    CRON = "cron"


class ScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


TERMINAL_SCHEDULE_STATUSES = frozenset({ScheduleStatus.COMPLETED, ScheduleStatus.CANCELLED})


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise DomainValidationError("Schedule.timezone must be an IANA timezone") from exc
    return value


@dataclass(slots=True)
class Schedule:
    _id: ScheduleId
    _task_id: TaskId
    _kind: ScheduleKind
    _cron: str | None
    _run_at: datetime | None
    _timezone: str
    _status: ScheduleStatus
    _next_fire_at: datetime | None
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def new(
        cls,
        *,
        id: ScheduleId,
        task_id: TaskId,
        kind: ScheduleKind,
        cron: str | None,
        run_at: datetime | None,
        timezone: str,
        now: datetime,
        next_fire_at: datetime | None,
    ) -> Schedule:
        now = ensure_utc(now)
        timezone = validate_timezone(timezone)
        if kind is ScheduleKind.ONCE:
            if run_at is None or cron is not None:
                raise DomainValidationError("once schedules require run_at and forbid cron")
            run_at = ensure_utc(run_at)
        elif kind is ScheduleKind.CRON:
            if cron is None or run_at is not None:
                raise DomainValidationError("cron schedules require cron and forbid run_at")
            cron = cron.strip()
        else:
            raise DomainValidationError("Schedule.kind is invalid")
        next_fire_at = ensure_utc(next_fire_at) if next_fire_at else None
        schedule_status = ScheduleStatus.ACTIVE if next_fire_at else ScheduleStatus.COMPLETED
        return cls(
            id, task_id, kind, cron, run_at, timezone, schedule_status, next_fire_at, now, now
        )

    @property
    def id(self) -> ScheduleId:
        return self._id

    @property
    def task_id(self) -> TaskId:
        return self._task_id

    @property
    def kind(self) -> ScheduleKind:
        return self._kind

    @property
    def cron(self) -> str | None:
        return self._cron

    @property
    def run_at(self) -> datetime | None:
        return self._run_at

    @property
    def timezone(self) -> str:
        return self._timezone

    @property
    def status(self) -> ScheduleStatus:
        return self._status

    @property
    def next_fire_at(self) -> datetime | None:
        return self._next_fire_at

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def pause(self, at: datetime) -> None:
        if self._status is not ScheduleStatus.ACTIVE:
            raise InvalidStateTransition(
                "Schedule", self._status.value, ScheduleStatus.PAUSED.value
            )
        self._status, self._updated_at = ScheduleStatus.PAUSED, ensure_utc(at)

    def resume(self, at: datetime, next_fire_at: datetime | None) -> None:
        if self._status is not ScheduleStatus.PAUSED:
            raise InvalidStateTransition(
                "Schedule", self._status.value, ScheduleStatus.ACTIVE.value
            )
        self._next_fire_at = ensure_utc(next_fire_at) if next_fire_at else None
        self._status = ScheduleStatus.ACTIVE if self._next_fire_at else ScheduleStatus.COMPLETED
        self._updated_at = ensure_utc(at)

    def cancel(self, at: datetime) -> None:
        if self._status in TERMINAL_SCHEDULE_STATUSES:
            raise InvalidStateTransition(
                "Schedule", self._status.value, ScheduleStatus.CANCELLED.value
            )
        self._status, self._next_fire_at, self._updated_at = (
            ScheduleStatus.CANCELLED,
            None,
            ensure_utc(at),
        )

    def complete(self, at: datetime) -> None:
        self._status, self._next_fire_at, self._updated_at = (
            ScheduleStatus.COMPLETED,
            None,
            ensure_utc(at),
        )

    def advance_after_fire(self, *, now: datetime, next_fire_at: datetime | None) -> None:
        self._next_fire_at = ensure_utc(next_fire_at) if next_fire_at else None
        self._status = ScheduleStatus.ACTIVE if self._next_fire_at else ScheduleStatus.COMPLETED
        self._updated_at = ensure_utc(now)

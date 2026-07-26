"""Croniter-backed, timezone-aware recurrence calculation."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter  # type: ignore[import-untyped]

from friday.domain.errors import DomainValidationError
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.time import ensure_utc


def first_occurrence(
    kind: ScheduleKind, cron: str | None, run_at: datetime | None, timezone: str, now: datetime
) -> datetime | None:
    if kind is ScheduleKind.ONCE:
        return ensure_utc(run_at) if run_at and ensure_utc(run_at) > ensure_utc(now) else None
    assert cron is not None
    return _next_cron(cron, timezone, now)


def next_occurrence(schedule: Schedule, after: datetime) -> datetime | None:
    if schedule.kind is ScheduleKind.ONCE:
        return schedule.run_at if schedule.run_at and schedule.run_at > ensure_utc(after) else None
    assert schedule.cron is not None
    return _next_cron(schedule.cron, schedule.timezone, after)


def coalesced_next(schedule: Schedule, *, fired_at: datetime, now: datetime) -> datetime | None:
    value = next_occurrence(schedule, fired_at)
    while value is not None and value <= ensure_utc(now):
        value = next_occurrence(schedule, value)
    return value


def _next_cron(expression: str, timezone: str, after: datetime) -> datetime:
    if len(expression.split()) != 5:
        raise DomainValidationError("Schedule.cron must have exactly five fields (no seconds)")
    try:
        value = croniter(expression, ensure_utc(after).astimezone(ZoneInfo(timezone))).get_next(
            datetime
        )
    except (CroniterBadCronError, ValueError) as exc:
        raise DomainValidationError("Schedule.cron is invalid") from exc
    return ensure_utc(value.astimezone(UTC))

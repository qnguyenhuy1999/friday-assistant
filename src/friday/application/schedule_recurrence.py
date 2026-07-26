"""Croniter-backed, timezone-aware recurrence calculation."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, croniter  # type: ignore[import-untyped]

from friday.domain.errors import DomainValidationError
from friday.domain.schedule import Schedule, ScheduleKind, validate_timezone
from friday.domain.time import ensure_utc


def first_occurrence(
    kind: ScheduleKind, cron: str | None, run_at: datetime | None, timezone: str, now: datetime
) -> datetime | None:
    validate_timezone(timezone)
    if kind is ScheduleKind.ONCE:
        if run_at is None:
            return None
        value = _resolve_run_at(run_at, timezone)
        if value <= ensure_utc(now):
            raise DomainValidationError("Schedule.run_at must be in the future")
        return value
    assert cron is not None
    return _next_cron(cron, timezone, now)


def next_occurrence(schedule: Schedule, after: datetime) -> datetime | None:
    if schedule.kind is ScheduleKind.ONCE:
        return schedule.run_at if schedule.run_at and schedule.run_at > ensure_utc(after) else None
    assert schedule.cron is not None
    return _next_cron(schedule.cron, schedule.timezone, after)


def coalesced_next(schedule: Schedule, *, fired_at: datetime, now: datetime) -> datetime | None:
    # croniter computes the next future local occurrence directly from now;
    # do not walk every missed minute after a long worker outage.
    if schedule.kind is ScheduleKind.ONCE:
        return None
    assert schedule.cron is not None
    return _next_cron(schedule.cron, schedule.timezone, now)


def _resolve_run_at(value: datetime, timezone: str) -> datetime:
    """Interpret a naive one-shot value as wall time in its supplied IANA zone.

    Ambiguous DST fall-back wall times use the earlier instant (fold=0); a
    nonexistent spring-forward local time is rejected rather than silently
    shifted. API clients may also submit an offset-aware RFC3339 timestamp.
    """
    zone = ZoneInfo(timezone)
    if value.tzinfo is not None:
        return ensure_utc(value)
    local = value.replace(tzinfo=zone, fold=0)
    round_trip = local.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != value:
        raise DomainValidationError("Schedule.run_at is a nonexistent local time")
    return ensure_utc(local)


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

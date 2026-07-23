"""UTC timestamp normalization.

Policy: naive datetimes are rejected outright rather than silently assumed
to be UTC — an ambiguous input should fail fast, not produce a wrong-by-N-hours
timestamp days later. Aware datetimes in another offset are converted to UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

from friday.domain.errors import DomainValidationError


def ensure_utc(value: datetime) -> datetime:
    """Return `value` normalized to an aware UTC datetime.

    Raises DomainValidationError if `value` is naive (no tzinfo).
    """
    if value.tzinfo is None:
        raise DomainValidationError("timestamp must be timezone-aware, got a naive datetime")
    return value.astimezone(UTC)

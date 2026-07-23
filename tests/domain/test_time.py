"""UTC timestamp normalization: naive datetimes rejected, aware datetimes
converted to UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.time import ensure_utc


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        ensure_utc(datetime(2026, 1, 1))  # noqa: DTZ001


def test_utc_datetime_passes_through() -> None:
    value = datetime(2026, 1, 1, tzinfo=UTC)
    assert ensure_utc(value) == value


def test_non_utc_aware_datetime_is_converted_to_utc() -> None:
    plus_five = timezone(timedelta(hours=5))
    value = datetime(2026, 1, 1, 10, 0, tzinfo=plus_five)
    result = ensure_utc(value)
    assert result.tzinfo == UTC
    assert result == datetime(2026, 1, 1, 5, 0, tzinfo=UTC)

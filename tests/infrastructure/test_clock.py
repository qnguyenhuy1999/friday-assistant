from __future__ import annotations

from datetime import timedelta

from friday.infrastructure.clock import SystemClock


def test_now_returns_aware_utc_datetime() -> None:
    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)

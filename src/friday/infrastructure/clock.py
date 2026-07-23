"""Production `Clock` (see `friday.application.ports.Clock`): stdlib only."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

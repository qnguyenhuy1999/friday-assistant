from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.identifiers import RunId, ScheduleFireId, ScheduleId
from friday.domain.time import ensure_utc


@dataclass(frozen=True, slots=True)
class ScheduleFire:
    id: ScheduleFireId
    schedule_id: ScheduleId
    scheduled_for: datetime
    fired_at: datetime
    run_id: RunId

    @classmethod
    def new(
        cls,
        *,
        id: ScheduleFireId,
        schedule_id: ScheduleId,
        scheduled_for: datetime,
        fired_at: datetime,
        run_id: RunId,
    ) -> ScheduleFire:
        return cls(id, schedule_id, ensure_utc(scheduled_for), ensure_utc(fired_at), run_id)

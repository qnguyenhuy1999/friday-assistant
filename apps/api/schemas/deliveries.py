from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DeliveryResponse(BaseModel):
    id: str
    source_kind: str
    source_run_id: str
    source_schedule_fire_id: str | None
    route_id: str
    status: str
    available_at: datetime
    attempt_count: int
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None


class DeliveryPageResponse(BaseModel):
    items: list[DeliveryResponse]

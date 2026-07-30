from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CreateScheduleBody(BaseModel):
    kind: Literal["once", "cron"]
    cron: str | None = None
    run_at: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1)

    @model_validator(mode="after")
    def valid_shape(self) -> CreateScheduleBody:
        if self.kind == "once" and (self.run_at is None or self.cron is not None):
            raise ValueError("once requires run_at and forbids cron")
        if self.kind == "cron" and (self.cron is None or self.run_at is not None):
            raise ValueError("cron requires cron and forbids run_at")
        return self


class ScheduleResponse(BaseModel):
    id: str
    task_id: str
    kind: str
    cron: str | None
    run_at: datetime | None
    timezone: str
    status: str
    next_fire_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SchedulePageResponse(BaseModel):
    items: list[ScheduleResponse]
    next_cursor: str | None


class ScheduleFireResponse(BaseModel):
    id: str
    schedule_id: str
    scheduled_for: datetime
    fired_at: datetime
    run_id: str


class ScheduleFirePageResponse(BaseModel):
    items: list[ScheduleFireResponse]
    next_cursor: str | None


class PutScheduleDeliveryPolicyBody(BaseModel):
    route: str = Field(min_length=1, max_length=64)
    enabled: bool


class ScheduleDeliveryPolicyResponse(BaseModel):
    schedule_id: str
    route: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

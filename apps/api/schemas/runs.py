from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from apps.api.schemas.tasks import FailureBody


class RunResponse(BaseModel):
    id: str
    task_id: str
    status: str
    created_at: datetime
    failure: FailureBody | None


class RunResultResponse(BaseModel):
    summary: str | None
    details: Any


class RunPageResponse(BaseModel):
    items: list[RunResponse]
    next_cursor: str | None

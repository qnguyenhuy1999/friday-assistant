from __future__ import annotations

from pydantic import BaseModel, Field

from apps.api.schemas.tasks import FailureBody


class CreateStepBody(BaseModel):
    name: str = Field(min_length=1)


class StepResponse(BaseModel):
    id: str
    run_id: str
    name: str
    position: int
    status: str
    failure: FailureBody | None


class StepPageResponse(BaseModel):
    items: list[StepResponse]
    next_cursor: str | None

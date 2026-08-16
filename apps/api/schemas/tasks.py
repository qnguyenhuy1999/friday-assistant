from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from friday.domain.failure import FailureCause


class FailureBody(BaseModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    cause: FailureCause
    details: Any = None


class CreateTaskBody(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""


class TaskResponse(BaseModel):
    id: str
    title: str
    description: str
    status: str
    created_at: datetime
    failure: FailureBody | None


class TaskPageResponse(BaseModel):
    items: list[TaskResponse]
    next_cursor: str | None


class StartRunResponse(BaseModel):
    task_id: str
    run_id: str


class PutTaskWorkflowBody(BaseModel):
    workflow_id: str


class TaskWorkflowBindingResponse(BaseModel):
    task_id: str
    workflow_id: str
    created_at: datetime
    updated_at: datetime

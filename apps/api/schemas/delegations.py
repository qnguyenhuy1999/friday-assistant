from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateDelegationRequestBody(BaseModel):
    target_agent_id: str
    objective: str = Field(min_length=1, max_length=4000)
    input_payload: Any = None
    expected_output_contract: str = Field(min_length=1, max_length=4000)
    parent_run_step_id: str | None = None


class DelegationRequestResponse(BaseModel):
    id: str
    parent_run_id: str
    parent_run_step_id: str | None
    target_agent_id: str
    objective: str
    input_payload: Any
    expected_output_contract: str
    authorization_fingerprint: str
    status: str
    child_task_id: str | None
    child_run_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None

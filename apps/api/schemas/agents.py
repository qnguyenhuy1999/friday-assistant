from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateAgentBody(BaseModel):
    key: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)


class CreateAgentRevisionBody(BaseModel):
    instructions: str = Field(min_length=1, max_length=32000)
    runtime_kind: str = Field(min_length=1, max_length=64)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    source_kind: Literal["operator", "imported"]


class AgentResponse(BaseModel):
    id: str
    key: str
    display_name: str
    description: str
    status: str
    active_revision_id: str | None
    created_at: datetime
    updated_at: datetime


class AgentRevisionResponse(BaseModel):
    id: str
    agent_id: str
    version: int
    instructions: str
    runtime_kind: str
    runtime_config: Any
    content_sha256: str
    source_kind: str
    created_at: datetime


class AgentPageResponse(BaseModel):
    items: list[AgentResponse]
    next_cursor: str | None = None


class PutTaskAgentBody(BaseModel):
    agent_id: str | None = None


class TaskAgentBindingResponse(BaseModel):
    task_id: str
    agent_id: str
    created_at: datetime


class RunAgentResolutionResponse(BaseModel):
    run_id: str
    resolved: bool
    resolved_at: datetime | None
    agent_id: str | None
    revision_id: str | None

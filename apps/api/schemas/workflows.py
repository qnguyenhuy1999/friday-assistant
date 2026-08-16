from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateWorkflowBody(StrictModel):
    key: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)


class WorkflowNodeBody(StrictModel):
    node_key: str = Field(min_length=1, max_length=128)
    target_agent_id: str
    objective: str = Field(min_length=1, max_length=4000)
    input_payload: Any = Field(default_factory=dict)
    expected_output_contract: str = Field(min_length=1, max_length=4000)


class WorkflowEdgeBody(StrictModel):
    from_: str = Field(alias="from", min_length=1, max_length=128)
    to: str = Field(min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CreateWorkflowRevisionBody(StrictModel):
    nodes: list[WorkflowNodeBody]
    edges: list[WorkflowEdgeBody]
    source_kind: Literal["operator", "imported"] = "operator"


class WorkflowResponse(StrictModel):
    id: str
    key: str
    display_name: str
    description: str
    status: str
    active_revision_id: str | None
    created_at: datetime
    updated_at: datetime


class WorkflowNodeResponse(StrictModel):
    id: str
    revision_id: str
    node_key: str
    target_agent_id: str
    objective: str
    input_payload: Any
    expected_output_contract: str
    created_at: datetime


class WorkflowEdgeResponse(StrictModel):
    id: str
    revision_id: str
    from_: str = Field(alias="from")
    to: str
    created_at: datetime
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkflowRevisionResponse(StrictModel):
    id: str
    workflow_id: str
    version: int
    content_sha256: str
    source_kind: str
    nodes: list[WorkflowNodeResponse]
    edges: list[WorkflowEdgeResponse]
    created_at: datetime


class WorkflowPageResponse(StrictModel):
    items: list[WorkflowResponse]
    next_cursor: str | None = None


class WorkflowExecutionInspectionResponse(StrictModel):
    root_run_id: str
    workflow_execution_id: str
    workflow_id: str
    workflow_revision_id: str
    workflow_revision_sha256: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class WorkflowNodeExecutionInspectionResponse(StrictModel):
    node_execution_id: str
    node_key: str
    target_agent_id: str
    target_agent_revision_id: str
    target_agent_revision_sha256: str
    status: str
    child_task_id: str | None = None
    child_run_id: str | None = None
    child_execution_id: str | None = None
    result_payload: Any = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

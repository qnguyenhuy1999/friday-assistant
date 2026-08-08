from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CreateSkillBody(BaseModel):
    key: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)


class CreateSkillRevisionBody(BaseModel):
    instructions: str = Field(min_length=1, max_length=32000)
    source_kind: Literal["operator", "imported"]


class SkillResponse(BaseModel):
    id: str
    key: str
    display_name: str
    description: str
    status: str
    active_revision_id: str | None
    created_at: datetime
    updated_at: datetime


class SkillRevisionResponse(BaseModel):
    id: str
    skill_id: str
    version: int
    instructions: str
    content_sha256: str
    source_kind: str
    created_at: datetime


class SkillPageResponse(BaseModel):
    items: list[SkillResponse]
    next_cursor: str | None = None


class ReplaceTaskSkillsBody(BaseModel):
    skill_ids: list[str] = Field(max_length=16)


class TaskSkillBindingResponse(BaseModel):
    task_id: str
    skill_id: str
    position: int
    created_at: datetime


class RunSkillAuditItem(BaseModel):
    skill_id: str
    skill_key: str
    revision_id: str
    version: int
    instructions: str
    content_sha256: str
    source_kind: str
    position: int


class RunSkillBindingResponse(BaseModel):
    run_id: str
    resolved: bool
    resolved_at: datetime | None
    items: list[RunSkillAuditItem]


class SkillUsageRecordResponse(BaseModel):
    id: str
    run_id: str
    task_id: str
    skill_id: str
    revision_id: str
    position: int
    resolution_id: str
    execution_id: str
    attempt_number: int
    started_at: datetime | None
    outcome: str
    failure_code: str | None
    tool_call_count: int
    approval_count: int
    duration_ms: int | None
    completed_at: datetime
    created_at: datetime


class AddSkillFeedbackBody(BaseModel):
    rating: Literal["helpful", "neutral", "harmful"]
    note: str = Field(default="", max_length=4000)
    created_by: str = Field(min_length=1, max_length=128)


class SkillFeedbackResponse(BaseModel):
    id: str
    run_id: str
    skill_id: str
    revision_id: str
    rating: str
    note: str
    created_by: str
    created_at: datetime


class EvaluationCaseBody(BaseModel):
    input: str = Field(min_length=1, max_length=32000)
    expected_properties: dict[str, object] = Field(default_factory=dict)
    grading_kind: Literal[
        "exact_match",
        "contains_all",
        "contains_none",
        "json_schema",
        "required_keys",
        "tool_proposal_shape",
    ]


class CreateEvaluationSuiteBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=4000)
    cases: list[EvaluationCaseBody] = Field(min_length=1, max_length=100)


class EvaluationSuiteResponse(BaseModel):
    id: str
    skill_id: str
    name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    cases: list[EvaluationCaseResponse] = Field(default_factory=list)


class EvaluationCaseResponse(BaseModel):
    id: str
    suite_id: str
    position: int
    input: str
    expected_properties: object
    grading_kind: str
    created_at: datetime
    updated_at: datetime


class RunEvaluationBody(BaseModel):
    revision_id: str | None = None
    proposal_id: str | None = None
    outputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target(self) -> RunEvaluationBody:
        if (self.revision_id is None) == (self.proposal_id is None):
            raise ValueError("exactly one evaluation target is required")
        return self


class EvaluationRunResponse(BaseModel):
    id: str
    suite_id: str
    skill_id: str
    revision_id: str | None
    proposal_id: str | None
    status: str
    aggregate_result: object
    runtime_fingerprint: str
    target_content_sha256: str
    runtime_metadata: object
    suite_snapshot: object
    case_results: list[EvaluationCaseResultResponse]


class EvaluationCaseResultResponse(BaseModel):
    evaluation_run_id: str
    case_id: str
    status: str
    score: float
    reason_code: str | None
    bounded_details: str
    output_sha256: str


class CreateImprovementProposalBody(BaseModel):
    base_revision_id: str
    trigger_kind: str = Field(min_length=1, max_length=128)
    evidence_snapshot_id: str
    evidence_snapshot_hash: str = Field(min_length=64, max_length=64)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    generator_version: str = Field(min_length=1, max_length=128)
    candidate_json: str = Field(min_length=2, max_length=50000)


class CreateSkillEvidenceSnapshotBody(BaseModel):
    base_revision_id: str
    evidence: object


class SkillEvidenceSnapshotResponse(BaseModel):
    id: str
    skill_id: str
    base_revision_id: str
    content_sha256: str
    evidence: object
    created_at: datetime


class ImprovementProposalResponse(BaseModel):
    id: str
    skill_id: str
    base_revision_id: str
    status: str
    evidence_snapshot_id: str
    evidence_snapshot_hash: str
    proposed_instructions: str
    proposed_content_sha256: str
    rationale: str
    generator_version: str
    created_at: datetime


class EvaluateImprovementProposalBody(BaseModel):
    baseline_evaluation_run_id: str
    candidate_outputs: dict[str, str] = Field(default_factory=dict)
    comparison_policy_version: str = Field(default="comparison-v1", min_length=1, max_length=128)


class CandidateEvaluationResponse(BaseModel):
    id: str
    proposal_id: str
    baseline_evaluation_run_id: str
    candidate_evaluation_run_id: str
    comparison_policy_version: str
    result: str
    recommendation: str
    score_delta: float
    regression_count: int
    improvement_count: int
    inconclusive_count: int
    report_sha256: str
    comparison_report: object


class ResolveSkillRequestBody(BaseModel):
    resolver: str = Field(min_length=1, max_length=128)


class RequestRollbackBody(BaseModel):
    target_revision_id: str
    reason: str = Field(min_length=1, max_length=4000)


class SkillPromotionResponse(BaseModel):
    id: str
    proposal_id: str
    skill_id: str
    status: str
    authorization_fingerprint: str
    target_version: int
    promoted_revision_id: str | None
    approval_request_id: str | None
    candidate_instructions: str
    candidate_sha256: str
    evidence_snapshot_id: str
    evidence_snapshot_hash: str
    comparison_report: object
    recommendation: str


class SkillRollbackResponse(BaseModel):
    id: str
    skill_id: str
    expected_current_revision_id: str
    target_revision_id: str
    reason: str
    status: str
    authorization_fingerprint: str
    approval_request_id: str | None
    resolved_at: datetime | None
    resolver: str | None


class SkillImprovementPolicyBody(BaseModel):
    enabled: bool = True
    minimum_usage_records: int = Field(default=1, ge=0)
    minimum_failures: int = Field(default=0, ge=0)
    minimum_harmful_feedback: int = Field(default=0, ge=0)
    evaluation_suite_id: str
    cooldown_seconds: int = Field(default=3600, ge=0)
    max_open_proposals: int = Field(default=1, ge=1, le=1)
    evidence_window_size: int = Field(default=20, ge=1, le=200)
    generator_version: str = Field(min_length=1, max_length=128)
    comparison_policy_version: str = Field(min_length=1, max_length=128)


class SkillImprovementPolicyResponse(SkillImprovementPolicyBody):
    skill_id: str
    last_triggered_at: datetime | None

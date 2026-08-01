"""Durable, immutable snapshots for isolated deterministic skill evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    SkillCandidateEvaluationId,
    SkillEvaluationCaseId,
    SkillEvaluationRunId,
    SkillEvaluationSuiteId,
    SkillId,
    SkillImprovementProposalId,
    SkillRevisionId,
)
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc


class EvaluationSuiteStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class EvaluationRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SkillEvaluationSuite:
    id: SkillEvaluationSuiteId
    skill_id: SkillId
    name: str
    description: str
    status: EvaluationSuiteStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 256:
            raise DomainValidationError("evaluation suite name must be present and bounded")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))


@dataclass(frozen=True, slots=True)
class SkillEvaluationCase:
    id: SkillEvaluationCaseId
    suite_id: SkillEvaluationSuiteId
    position: int
    input: str
    expected_properties: JsonValue
    grading_kind: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.position < 1 or not self.input or not self.grading_kind:
            raise DomainValidationError(
                "evaluation case requires position, input, and grading kind"
            )
        object.__setattr__(self, "expected_properties", ensure_json_value(self.expected_properties))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))


@dataclass(frozen=True, slots=True)
class SkillEvaluationRun:
    id: SkillEvaluationRunId
    suite_id: SkillEvaluationSuiteId
    skill_id: SkillId
    revision_id: SkillRevisionId | None
    proposal_id: SkillImprovementProposalId | None
    status: EvaluationRunStatus
    evaluator_version: str
    started_at: datetime
    completed_at: datetime
    aggregate_result: JsonValue
    suite_snapshot: JsonValue
    runtime_fingerprint: str

    def __post_init__(self) -> None:
        if not self.evaluator_version or len(self.runtime_fingerprint) != 64:
            raise DomainValidationError(
                "evaluation run requires evaluator version and sha256 fingerprint"
            )
        if (self.revision_id is None) == (self.proposal_id is None):
            raise DomainValidationError(
                "evaluation run must target exactly one revision or proposal"
            )
        object.__setattr__(self, "aggregate_result", ensure_json_value(self.aggregate_result))
        object.__setattr__(self, "suite_snapshot", ensure_json_value(self.suite_snapshot))
        object.__setattr__(self, "started_at", ensure_utc(self.started_at))
        object.__setattr__(self, "completed_at", ensure_utc(self.completed_at))


@dataclass(frozen=True, slots=True)
class SkillEvaluationCaseResult:
    evaluation_run_id: SkillEvaluationRunId
    case_id: SkillEvaluationCaseId
    status: EvaluationRunStatus
    score: float
    reason_code: str | None
    bounded_details: str
    output_sha256: str

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1 or len(self.bounded_details) > 4000:
            raise DomainValidationError("invalid evaluation case result")
        if len(self.output_sha256) != 64:
            raise DomainValidationError("evaluation output hash must be sha256")


class CandidateComparisonResult(StrEnum):
    BETTER = "better"
    WORSE = "worse"
    EQUIVALENT = "equivalent"
    MIXED = "mixed"
    INCONCLUSIVE = "inconclusive"


class CandidateRecommendation(StrEnum):
    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    REQUIRES_MANUAL_OVERRIDE = "requires_manual_override"


@dataclass(frozen=True, slots=True)
class SkillCandidateEvaluation:
    id: SkillCandidateEvaluationId
    proposal_id: SkillImprovementProposalId
    baseline_evaluation_run_id: SkillEvaluationRunId
    candidate_evaluation_run_id: SkillEvaluationRunId
    comparison_policy_version: str
    result: CandidateComparisonResult
    recommendation: CandidateRecommendation
    score_delta: float
    regression_count: int
    improvement_count: int
    inconclusive_count: int
    report_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.comparison_policy_version or len(self.report_sha256) != 64:
            raise DomainValidationError(
                "candidate comparison requires a policy version and sha256 report"
            )
        if min(self.regression_count, self.improvement_count, self.inconclusive_count) < 0:
            raise DomainValidationError("candidate comparison counts must be non-negative")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

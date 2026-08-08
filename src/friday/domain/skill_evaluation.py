"""Durable, immutable snapshots for isolated deterministic skill evaluations."""

from __future__ import annotations

import hashlib
import json
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


CANONICAL_DETERMINISTIC_EVALUATOR_VERSION = "deterministic-evaluator-v2"
CANONICAL_BRAIN_EVALUATOR_VERSION = "brain-only-evaluator-v2"


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
    target_content_sha256: str
    runtime_metadata: JsonValue = None
    call_usage: JsonValue = None

    def __post_init__(self) -> None:
        if (
            not self.evaluator_version
            or len(self.runtime_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.runtime_fingerprint)
            or len(self.target_content_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.target_content_sha256)
        ):
            raise DomainValidationError(
                "evaluation run requires evaluator version and sha256 fingerprint"
            )
        if (self.revision_id is None) == (self.proposal_id is None):
            raise DomainValidationError(
                "evaluation run must target exactly one revision or proposal"
            )
        object.__setattr__(self, "aggregate_result", ensure_json_value(self.aggregate_result))
        snapshot = ensure_json_value(self.suite_snapshot)
        if not isinstance(snapshot, dict) or set(snapshot) != {"cases"}:
            raise DomainValidationError("evaluation run suite snapshot is malformed")
        cases = snapshot.get("cases")
        if not isinstance(cases, list) or not cases:
            raise DomainValidationError("evaluation run suite snapshot is malformed")
        ids: set[str] = set()
        for position, item in enumerate(cases, start=1):
            case_id = item.get("id") if isinstance(item, dict) else None
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "position", "input", "expected_properties", "grading_kind"}
                or not isinstance(case_id, str)
                or case_id in ids
                or item.get("position") != position
                or not isinstance(item.get("input"), str)
                or not item["input"]
                or not isinstance(item.get("expected_properties"), dict)
                or not isinstance(item.get("grading_kind"), str)
            ):
                raise DomainValidationError("evaluation run suite snapshot is malformed")
            ids.add(case_id)
        object.__setattr__(self, "suite_snapshot", snapshot)
        runtime_metadata = ensure_json_value(self.runtime_metadata)
        if not isinstance(runtime_metadata, dict):
            raise DomainValidationError("evaluation run runtime metadata is malformed")
        object.__setattr__(self, "runtime_metadata", runtime_metadata)
        call_usage = ensure_json_value(self.call_usage)
        if call_usage is not None and not isinstance(call_usage, dict):
            raise DomainValidationError("evaluation run call usage is malformed")
        object.__setattr__(self, "call_usage", call_usage)
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
        if len(self.output_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.output_sha256
        ):
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
    comparison_report: JsonValue

    def __post_init__(self) -> None:
        if (
            not self.comparison_policy_version
            or len(self.report_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.report_sha256)
        ):
            raise DomainValidationError(
                "candidate comparison requires a policy version and sha256 report"
            )
        if min(self.regression_count, self.improvement_count, self.inconclusive_count) < 0:
            raise DomainValidationError("candidate comparison counts must be non-negative")
        report = ensure_json_value(self.comparison_report)
        if not isinstance(report, dict):
            raise DomainValidationError("candidate comparison report must be an object")
        report_hash = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if report_hash != self.report_sha256:
            raise DomainValidationError("candidate comparison report hash mismatch")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "comparison_report", report)

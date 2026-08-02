"""Explicit domain <-> ORM mappers, one pair of functions per entity.

No generic mapper abstraction: each entity gets its own `X_to_row`/`X_from_row`
pair so field renames or type changes surface as a type error at the call
site, not inside a shared reflection-based converter.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from friday.application.errors import SkillIntegrityFailed
from friday.application.memory.models import (
    IndexSnapshot,
    IndexState,
    MemoryRetrievalItem,
    MemoryRetrievalRecord,
    RetrievalMethod,
)
from friday.application.ports import RunWorkItemView
from friday.domain import (
    ApprovalCategory,
    ApprovalRequest,
    ApprovalRequestId,
    ApprovalStatus,
    ApprovalSubjectKind,
    Artifact,
    ArtifactId,
    ArtifactKind,
    CandidateComparisonResult,
    CandidateRecommendation,
    Conversation,
    ConversationId,
    ConversationInputMode,
    ConversationTurn,
    ConversationTurnId,
    DeliveryAttempt,
    DeliveryAttemptId,
    DeliveryAttemptOutcome,
    DeliveryId,
    DeliverySourceKind,
    DeliveryStatus,
    Failure,
    FailureCause,
    OutboundDelivery,
    Run,
    RunEvent,
    RunEventId,
    RunEventType,
    RunId,
    RunSkillBinding,
    RunSkillResolution,
    RunSkillResolutionId,
    RunStatus,
    RunStep,
    RunStepId,
    RunStepStatus,
    Schedule,
    ScheduleDeliveryPolicy,
    ScheduleFire,
    ScheduleFireDeliveryPlan,
    ScheduleFireDeliveryPlanId,
    ScheduleFireId,
    ScheduleId,
    ScheduleKind,
    ScheduleStatus,
    SkillCandidateEvaluation,
    SkillCandidateEvaluationId,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationCaseResult,
    SkillEvaluationRun,
    SkillEvaluationRunId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillEvidenceSnapshot,
    SkillEvidenceSnapshotId,
    SkillId,
    SkillImprovementPolicy,
    SkillImprovementProposal,
    SkillImprovementProposalId,
    SkillImprovementWork,
    SkillImprovementWorkId,
    SkillPromotionRequest,
    SkillPromotionRequestId,
    SkillProposalStatus,
    SkillRevisionId,
    SkillRollbackRequest,
    SkillRollbackRequestId,
    SkillRunFeedback,
    SkillRunFeedbackId,
    SkillUsageOutcome,
    SkillUsageRecord,
    SkillUsageRecordId,
    Task,
    TaskEvent,
    TaskEventId,
    TaskEventType,
    TaskId,
    TaskSkillBinding,
    TaskStatus,
    ToolInvocation,
    ToolInvocationId,
    ToolInvocationStatus,
)
from friday.domain.json_value import JsonValue
from friday.domain.schedule_fire_delivery_plan import (
    ScheduleFireDeliveryContentSource,
    ScheduleFireDeliveryPlanStatus,
)
from friday.domain.skill import Skill, SkillRevision, SkillRevisionSourceKind, SkillStatus
from friday.domain.skill_promotion import PromotionRequestStatus, RollbackRequestStatus
from friday.domain.tool_provenance import ToolProvenance
from friday.infrastructure.persistence.models import (
    ApprovalRequestRow,
    ArtifactRow,
    ConversationRow,
    ConversationTurnRow,
    DeliveryAttemptRow,
    MemoryIndexSnapshotRow,
    MemoryRetrievalItemRow,
    MemoryRetrievalRecordRow,
    OutboundDeliveryRow,
    RunEventRow,
    RunRow,
    RunSkillBindingRow,
    RunSkillResolutionRow,
    RunStepRow,
    RunWorkItemRow,
    ScheduleDeliveryPolicyRow,
    ScheduleFireDeliveryPlanRow,
    ScheduleFireRow,
    ScheduleRow,
    SkillCandidateEvaluationRow,
    SkillEvaluationCaseResultRow,
    SkillEvaluationCaseRow,
    SkillEvaluationRunRow,
    SkillEvaluationSuiteRow,
    SkillEvidenceSnapshotRow,
    SkillImprovementPolicyRow,
    SkillImprovementProposalRow,
    SkillImprovementWorkRow,
    SkillPromotionRequestRow,
    SkillRevisionRow,
    SkillRollbackRequestRow,
    SkillRow,
    SkillRunFeedbackRow,
    SkillUsageRecordRow,
    TaskEventRow,
    TaskRow,
    TaskSkillBindingRow,
    ToolInvocationRow,
)


def skill_to_row(value: Skill) -> SkillRow:
    return SkillRow(
        id=str(value.id),
        key=value.key,
        display_name=value.display_name,
        description=value.description,
        status=value.status.value,
        active_revision_id=str(value.active_revision_id) if value.active_revision_id else None,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def skill_from_row(row: SkillRow) -> Skill:
    return Skill(
        _id=SkillId.parse(row.id),
        _key=row.key,
        _display_name=row.display_name,
        _description=row.description,
        _status=SkillStatus(row.status),
        _active_revision_id=SkillRevisionId.parse(row.active_revision_id)
        if row.active_revision_id
        else None,
        _created_at=read_back_utc(row.created_at),
        _updated_at=read_back_utc(row.updated_at),
    )


def skill_revision_to_row(value: SkillRevision) -> SkillRevisionRow:
    return SkillRevisionRow(
        id=str(value.id),
        skill_id=str(value.skill_id),
        version=value.version,
        instructions=value.instructions,
        content_sha256=value.content_sha256,
        source_kind=value.source_kind.value,
        created_at=value.created_at,
        promotion_request_id=value.promotion_request_id,
    )


def skill_revision_from_row(row: SkillRevisionRow) -> SkillRevision:
    if hashlib.sha256(row.instructions.encode("utf-8")).hexdigest() != row.content_sha256:
        raise SkillIntegrityFailed()
    return SkillRevision(
        SkillRevisionId.parse(row.id),
        SkillId.parse(row.skill_id),
        row.version,
        row.instructions,
        row.content_sha256,
        SkillRevisionSourceKind(row.source_kind),
        read_back_utc(row.created_at),
        row.promotion_request_id,
    )


def task_skill_binding_to_row(value: TaskSkillBinding) -> TaskSkillBindingRow:
    return TaskSkillBindingRow(
        task_id=str(value.task_id),
        skill_id=str(value.skill_id),
        position=value.position,
        created_at=value.created_at,
    )


def task_skill_binding_from_row(row: TaskSkillBindingRow) -> TaskSkillBinding:
    return TaskSkillBinding(
        TaskId.parse(row.task_id),
        SkillId.parse(row.skill_id),
        row.position,
        read_back_utc(row.created_at),
    )


def run_skill_resolution_to_row(value: RunSkillResolution) -> RunSkillResolutionRow:
    return RunSkillResolutionRow(
        id=str(value.id), run_id=str(value.run_id), resolved_at=value.resolved_at
    )


def run_skill_resolution_from_row(row: RunSkillResolutionRow) -> RunSkillResolution:
    return RunSkillResolution(
        RunSkillResolutionId.parse(row.id), RunId.parse(row.run_id), read_back_utc(row.resolved_at)
    )


def run_skill_binding_to_row(value: RunSkillBinding) -> RunSkillBindingRow:
    return RunSkillBindingRow(
        run_id=str(value.run_id),
        skill_id=str(value.skill_id),
        revision_id=str(value.revision_id),
        position=value.position,
    )


def run_skill_binding_from_row(row: RunSkillBindingRow) -> RunSkillBinding:
    return RunSkillBinding(
        RunId.parse(row.run_id),
        SkillId.parse(row.skill_id),
        SkillRevisionId.parse(row.revision_id),
        row.position,
    )


def skill_usage_record_to_row(value: SkillUsageRecord) -> SkillUsageRecordRow:
    return SkillUsageRecordRow(
        id=str(value.id),
        run_id=str(value.run_id),
        task_id=str(value.task_id),
        skill_id=str(value.skill_id),
        revision_id=str(value.revision_id),
        position=value.position,
        resolution_id=value.resolution_id,
        execution_id=str(value.execution_id),
        attempt_number=value.attempt_number,
        started_at=value.started_at,
        completed_at=value.completed_at,
        outcome=value.outcome.value,
        failure_code=value.failure_code,
        tool_call_count=value.tool_call_count,
        approval_count=value.approval_count,
        duration_ms=value.duration_ms,
        created_at=value.created_at,
    )


def skill_usage_record_from_row(row: SkillUsageRecordRow) -> SkillUsageRecord:
    return SkillUsageRecord(
        id=SkillUsageRecordId.parse(row.id),
        run_id=RunId.parse(row.run_id),
        task_id=TaskId.parse(row.task_id),
        skill_id=SkillId.parse(row.skill_id),
        revision_id=SkillRevisionId.parse(row.revision_id),
        position=row.position,
        resolution_id=row.resolution_id,
        execution_id=RunId.parse(row.execution_id),
        attempt_number=row.attempt_number,
        started_at=read_back_utc(row.started_at) if row.started_at else None,
        completed_at=read_back_utc(row.completed_at),
        outcome=SkillUsageOutcome(row.outcome),
        failure_code=row.failure_code,
        tool_call_count=row.tool_call_count,
        approval_count=row.approval_count,
        duration_ms=row.duration_ms,
        created_at=read_back_utc(row.created_at),
    )


def skill_run_feedback_to_row(value: SkillRunFeedback) -> SkillRunFeedbackRow:
    return SkillRunFeedbackRow(
        id=str(value.id),
        run_id=str(value.run_id),
        skill_id=str(value.skill_id),
        revision_id=str(value.revision_id),
        rating=value.rating.value,
        note=value.note,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def skill_run_feedback_from_row(row: SkillRunFeedbackRow) -> SkillRunFeedback:
    from friday.domain import SkillFeedbackRating

    return SkillRunFeedback(
        id=SkillRunFeedbackId.parse(row.id),
        run_id=RunId.parse(row.run_id),
        skill_id=SkillId.parse(row.skill_id),
        revision_id=SkillRevisionId.parse(row.revision_id),
        rating=SkillFeedbackRating(row.rating),
        note=row.note,
        created_by=row.created_by,
        created_at=read_back_utc(row.created_at),
    )


def skill_evaluation_suite_to_row(value: SkillEvaluationSuite) -> SkillEvaluationSuiteRow:
    return SkillEvaluationSuiteRow(
        id=str(value.id),
        skill_id=str(value.skill_id),
        name=value.name,
        description=value.description,
        status=value.status.value,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def skill_evaluation_suite_from_row(row: SkillEvaluationSuiteRow) -> SkillEvaluationSuite:
    from friday.domain import EvaluationSuiteStatus

    return SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.parse(row.id),
        skill_id=SkillId.parse(row.skill_id),
        name=row.name,
        description=row.description,
        status=EvaluationSuiteStatus(row.status),
        created_at=read_back_utc(row.created_at),
        updated_at=read_back_utc(row.updated_at),
    )


def skill_evaluation_case_to_row(value: SkillEvaluationCase) -> SkillEvaluationCaseRow:
    return SkillEvaluationCaseRow(
        id=str(value.id),
        suite_id=str(value.suite_id),
        position=value.position,
        input=value.input,
        expected_properties=value.expected_properties,
        grading_kind=value.grading_kind,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def skill_evaluation_case_from_row(row: SkillEvaluationCaseRow) -> SkillEvaluationCase:
    return SkillEvaluationCase(
        id=SkillEvaluationCaseId.parse(row.id),
        suite_id=SkillEvaluationSuiteId.parse(row.suite_id),
        position=row.position,
        input=row.input,
        expected_properties=cast(JsonValue, row.expected_properties),
        grading_kind=row.grading_kind,
        created_at=read_back_utc(row.created_at),
        updated_at=read_back_utc(row.updated_at),
    )


def skill_evaluation_run_to_row(value: SkillEvaluationRun) -> SkillEvaluationRunRow:
    return SkillEvaluationRunRow(
        id=str(value.id),
        suite_id=str(value.suite_id),
        skill_id=str(value.skill_id),
        revision_id=str(value.revision_id) if value.revision_id else None,
        proposal_id=str(value.proposal_id) if value.proposal_id else None,
        status=value.status.value,
        evaluator_version=value.evaluator_version,
        started_at=value.started_at,
        completed_at=value.completed_at,
        aggregate_result=value.aggregate_result,
        suite_snapshot=value.suite_snapshot,
        runtime_fingerprint=value.runtime_fingerprint,
        target_content_sha256=value.target_content_sha256,
        runtime_metadata=value.runtime_metadata,
        call_usage=value.call_usage,
    )


def skill_evaluation_run_from_row(row: SkillEvaluationRunRow) -> SkillEvaluationRun:
    from friday.domain import EvaluationRunStatus

    return SkillEvaluationRun(
        id=SkillEvaluationRunId.parse(row.id),
        suite_id=SkillEvaluationSuiteId.parse(row.suite_id),
        skill_id=SkillId.parse(row.skill_id),
        revision_id=SkillRevisionId.parse(row.revision_id) if row.revision_id else None,
        proposal_id=SkillImprovementProposalId.parse(row.proposal_id) if row.proposal_id else None,
        status=EvaluationRunStatus(row.status),
        evaluator_version=row.evaluator_version,
        started_at=read_back_utc(row.started_at),
        completed_at=read_back_utc(row.completed_at),
        aggregate_result=cast(JsonValue, row.aggregate_result),
        suite_snapshot=cast(JsonValue, row.suite_snapshot),
        runtime_fingerprint=row.runtime_fingerprint,
        target_content_sha256=row.target_content_sha256,
        runtime_metadata=cast(JsonValue, row.runtime_metadata),
        call_usage=cast(JsonValue, row.call_usage),
    )


def skill_evaluation_case_result_to_row(
    value: SkillEvaluationCaseResult,
) -> SkillEvaluationCaseResultRow:
    return SkillEvaluationCaseResultRow(
        evaluation_run_id=str(value.evaluation_run_id),
        case_id=str(value.case_id),
        status=value.status.value,
        score=value.score,
        reason_code=value.reason_code,
        bounded_details=value.bounded_details,
        output_sha256=value.output_sha256,
    )


def skill_evaluation_case_result_from_row(
    row: SkillEvaluationCaseResultRow,
) -> SkillEvaluationCaseResult:
    from friday.domain import EvaluationRunStatus

    return SkillEvaluationCaseResult(
        evaluation_run_id=SkillEvaluationRunId.parse(row.evaluation_run_id),
        case_id=SkillEvaluationCaseId.parse(row.case_id),
        status=EvaluationRunStatus(row.status),
        score=row.score,
        reason_code=row.reason_code,
        bounded_details=row.bounded_details,
        output_sha256=row.output_sha256,
    )


def skill_improvement_proposal_to_row(
    value: SkillImprovementProposal,
) -> SkillImprovementProposalRow:
    return SkillImprovementProposalRow(
        id=str(value.id),
        skill_id=str(value.skill_id),
        base_revision_id=str(value.base_revision_id),
        status=value.status.value,
        trigger_kind=value.trigger_kind,
        evidence_snapshot_id=str(value.evidence_snapshot_id),
        evidence_snapshot_hash=value.evidence_snapshot_hash,
        proposed_instructions=value.proposed_instructions,
        proposed_content_sha256=value.proposed_content_sha256,
        rationale=value.rationale,
        generator_version=value.generator_version,
        created_at=value.created_at,
    )


def skill_improvement_proposal_from_row(
    row: SkillImprovementProposalRow,
) -> SkillImprovementProposal:
    if row.evidence_snapshot_id is None:
        raise ValueError("legacy proposal is missing its evidence snapshot link")
    if hashlib.sha256(row.proposed_instructions.encode("utf-8")).hexdigest() != (
        row.proposed_content_sha256
    ):
        raise SkillIntegrityFailed()
    return SkillImprovementProposal(
        id=SkillImprovementProposalId.parse(row.id),
        skill_id=SkillId.parse(row.skill_id),
        base_revision_id=SkillRevisionId.parse(row.base_revision_id),
        status=SkillProposalStatus(row.status),
        trigger_kind=row.trigger_kind,
        evidence_snapshot_id=SkillEvidenceSnapshotId.parse(row.evidence_snapshot_id),
        evidence_snapshot_hash=row.evidence_snapshot_hash,
        proposed_instructions=row.proposed_instructions,
        proposed_content_sha256=row.proposed_content_sha256,
        rationale=row.rationale,
        generator_version=row.generator_version,
        created_at=read_back_utc(row.created_at),
    )


def skill_improvement_work_to_row(value: SkillImprovementWork) -> SkillImprovementWorkRow:
    return SkillImprovementWorkRow(
        id=str(value.id),
        skill_id=str(value.skill_id),
        state=value.state.value,
        proposal_id=str(value.proposal_id) if value.proposal_id else None,
        attempt_count=value.attempt_count,
        next_attempt_at=value.next_attempt_at,
        claimed_by=value.claimed_by,
        claim_token=value.claim_token,
        claim_generation=value.claim_generation,
        lease_expires_at=value.lease_expires_at,
        failure_code=value.failure_code,
        failure_detail=value.failure_detail,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def skill_improvement_work_from_row(row: SkillImprovementWorkRow) -> SkillImprovementWork:
    from friday.domain import SkillImprovementWorkState

    return SkillImprovementWork(
        id=SkillImprovementWorkId.parse(row.id),
        skill_id=SkillId.parse(row.skill_id),
        state=SkillImprovementWorkState(row.state),
        proposal_id=SkillImprovementProposalId.parse(row.proposal_id) if row.proposal_id else None,
        attempt_count=row.attempt_count,
        next_attempt_at=read_back_utc(row.next_attempt_at),
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        claim_generation=row.claim_generation,
        lease_expires_at=read_back_utc(row.lease_expires_at) if row.lease_expires_at else None,
        failure_code=row.failure_code,
        failure_detail=row.failure_detail,
        created_at=read_back_utc(row.created_at),
        updated_at=read_back_utc(row.updated_at),
    )


def skill_candidate_evaluation_to_row(
    value: SkillCandidateEvaluation,
) -> SkillCandidateEvaluationRow:
    return SkillCandidateEvaluationRow(
        id=str(value.id),
        proposal_id=str(value.proposal_id),
        baseline_evaluation_run_id=str(value.baseline_evaluation_run_id),
        candidate_evaluation_run_id=str(value.candidate_evaluation_run_id),
        comparison_policy_version=value.comparison_policy_version,
        result=value.result.value,
        recommendation=value.recommendation.value,
        score_delta=value.score_delta,
        regression_count=value.regression_count,
        improvement_count=value.improvement_count,
        inconclusive_count=value.inconclusive_count,
        report_sha256=value.report_sha256,
        created_at=value.created_at,
        comparison_report=value.comparison_report,
    )


def skill_candidate_evaluation_from_row(
    row: SkillCandidateEvaluationRow,
) -> SkillCandidateEvaluation:
    if row.comparison_report is not None:
        report_bytes = json.dumps(
            row.comparison_report, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if hashlib.sha256(report_bytes).hexdigest() != row.report_sha256:
            raise SkillIntegrityFailed()
    return SkillCandidateEvaluation(
        id=SkillCandidateEvaluationId.parse(row.id),
        proposal_id=SkillImprovementProposalId.parse(row.proposal_id),
        baseline_evaluation_run_id=SkillEvaluationRunId.parse(row.baseline_evaluation_run_id),
        candidate_evaluation_run_id=SkillEvaluationRunId.parse(row.candidate_evaluation_run_id),
        comparison_policy_version=row.comparison_policy_version,
        result=CandidateComparisonResult(row.result),
        recommendation=CandidateRecommendation(row.recommendation),
        score_delta=row.score_delta,
        regression_count=row.regression_count,
        improvement_count=row.improvement_count,
        inconclusive_count=row.inconclusive_count,
        report_sha256=row.report_sha256,
        created_at=read_back_utc(row.created_at),
        comparison_report=cast(JsonValue, row.comparison_report),
    )


def skill_promotion_request_to_row(value: SkillPromotionRequest) -> SkillPromotionRequestRow:
    return SkillPromotionRequestRow(
        id=str(value.id),
        proposal_id=str(value.proposal_id),
        skill_id=str(value.skill_id),
        base_revision_id=str(value.base_revision_id),
        expected_active_revision_id=str(value.expected_active_revision_id),
        candidate_sha256=value.candidate_sha256,
        candidate_evaluation_id=str(value.candidate_evaluation_id),
        comparison_report_sha256=value.comparison_report_sha256,
        target_revision_id=str(value.target_revision_id),
        target_version=value.target_version,
        authorization_fingerprint=value.authorization_fingerprint,
        status=value.status.value,
        created_at=value.created_at,
        resolved_at=value.resolved_at,
        resolver=value.resolver,
        promoted_revision_id=str(value.promoted_revision_id)
        if value.promoted_revision_id
        else None,
        approval_request_id=str(value.approval_request_id) if value.approval_request_id else None,
    )


def skill_promotion_request_from_row(row: SkillPromotionRequestRow) -> SkillPromotionRequest:
    return SkillPromotionRequest(
        id=SkillPromotionRequestId.parse(row.id),
        proposal_id=SkillImprovementProposalId.parse(row.proposal_id),
        skill_id=SkillId.parse(row.skill_id),
        base_revision_id=SkillRevisionId.parse(row.base_revision_id),
        expected_active_revision_id=SkillRevisionId.parse(row.expected_active_revision_id),
        candidate_sha256=row.candidate_sha256,
        candidate_evaluation_id=SkillCandidateEvaluationId.parse(row.candidate_evaluation_id),
        comparison_report_sha256=row.comparison_report_sha256,
        target_revision_id=SkillRevisionId.parse(row.target_revision_id),
        target_version=row.target_version,
        authorization_fingerprint=row.authorization_fingerprint,
        status=PromotionRequestStatus(row.status),
        created_at=read_back_utc(row.created_at),
        resolved_at=read_back_utc(row.resolved_at) if row.resolved_at else None,
        resolver=row.resolver,
        promoted_revision_id=SkillRevisionId.parse(row.promoted_revision_id)
        if row.promoted_revision_id
        else None,
        approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def skill_rollback_request_to_row(value: SkillRollbackRequest) -> SkillRollbackRequestRow:
    return SkillRollbackRequestRow(
        id=str(value.id),
        skill_id=str(value.skill_id),
        expected_current_revision_id=str(value.expected_current_revision_id),
        target_revision_id=str(value.target_revision_id),
        reason=value.reason,
        authorization_fingerprint=value.authorization_fingerprint,
        status=value.status.value,
        created_at=value.created_at,
        resolved_at=value.resolved_at,
        resolver=value.resolver,
        approval_request_id=str(value.approval_request_id) if value.approval_request_id else None,
    )


def skill_rollback_request_from_row(row: SkillRollbackRequestRow) -> SkillRollbackRequest:
    return SkillRollbackRequest(
        id=SkillRollbackRequestId.parse(row.id),
        skill_id=SkillId.parse(row.skill_id),
        expected_current_revision_id=SkillRevisionId.parse(row.expected_current_revision_id),
        target_revision_id=SkillRevisionId.parse(row.target_revision_id),
        reason=row.reason,
        authorization_fingerprint=row.authorization_fingerprint,
        status=RollbackRequestStatus(row.status),
        created_at=read_back_utc(row.created_at),
        resolved_at=read_back_utc(row.resolved_at) if row.resolved_at else None,
        resolver=row.resolver,
        approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def skill_improvement_policy_to_row(value: SkillImprovementPolicy) -> SkillImprovementPolicyRow:
    return SkillImprovementPolicyRow(
        skill_id=str(value.skill_id),
        enabled=value.enabled,
        minimum_usage_records=value.minimum_usage_records,
        minimum_failures=value.minimum_failures,
        minimum_harmful_feedback=value.minimum_harmful_feedback,
        evaluation_suite_id=str(value.evaluation_suite_id),
        cooldown_seconds=value.cooldown_seconds,
        max_open_proposals=value.max_open_proposals,
        evidence_window_size=value.evidence_window_size,
        generator_version=value.generator_version,
        comparison_policy_version=value.comparison_policy_version,
        created_at=value.created_at,
        updated_at=value.updated_at,
        last_triggered_at=value.last_triggered_at,
    )


def skill_improvement_policy_from_row(row: SkillImprovementPolicyRow) -> SkillImprovementPolicy:
    return SkillImprovementPolicy(
        skill_id=SkillId.parse(row.skill_id),
        enabled=row.enabled,
        minimum_usage_records=row.minimum_usage_records,
        minimum_failures=row.minimum_failures,
        minimum_harmful_feedback=row.minimum_harmful_feedback,
        evaluation_suite_id=SkillEvaluationSuiteId.parse(row.evaluation_suite_id),
        cooldown_seconds=row.cooldown_seconds,
        max_open_proposals=row.max_open_proposals,
        evidence_window_size=row.evidence_window_size,
        generator_version=row.generator_version,
        comparison_policy_version=row.comparison_policy_version,
        created_at=read_back_utc(row.created_at),
        updated_at=read_back_utc(row.updated_at),
        last_triggered_at=read_back_utc(row.last_triggered_at) if row.last_triggered_at else None,
    )


def skill_evidence_snapshot_to_row(value: SkillEvidenceSnapshot) -> SkillEvidenceSnapshotRow:
    return SkillEvidenceSnapshotRow(
        id=str(value.id),
        skill_id=str(value.skill_id),
        base_revision_id=str(value.base_revision_id),
        evidence=value.evidence,
        content_sha256=value.content_sha256,
        created_at=value.created_at,
    )


def skill_evidence_snapshot_from_row(row: SkillEvidenceSnapshotRow) -> SkillEvidenceSnapshot:
    return SkillEvidenceSnapshot(
        id=SkillEvidenceSnapshotId.parse(row.id),
        skill_id=SkillId.parse(row.skill_id),
        base_revision_id=SkillRevisionId.parse(row.base_revision_id),
        evidence=cast(JsonValue, row.evidence),
        content_sha256=row.content_sha256,
        created_at=read_back_utc(row.created_at),
    )


def _failure_to_dict(failure: Failure | None) -> dict[str, Any] | None:
    return asdict(failure) if failure is not None else None


def read_back_utc(value: datetime) -> datetime:
    """Reattach UTC tzinfo SQLite drops on read-back (values are always
    written UTC-normalized, so a naive read-back is safely reinterpreted).

    Public because repositories that validate a fenced write against a
    persisted timestamp must reinterpret it exactly the way the mappers do.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _failure_from_dict(data: dict[str, Any] | None) -> Failure | None:
    if data is None:
        return None
    return Failure(
        code=data["code"],
        message=data["message"],
        retryable=data["retryable"],
        cause=FailureCause(data["cause"]),
        details=data["details"],
    )


def task_to_row(task: Task) -> TaskRow:
    return TaskRow(
        id=str(task.id),
        title=task.title,
        description=task.description,
        status=task.status.value,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        failed_at=task.failed_at,
        cancelled_at=task.cancelled_at,
        failure=_failure_to_dict(task.failure),
    )


def task_from_row(row: TaskRow) -> Task:
    return Task(
        _id=TaskId.parse(row.id),
        _title=row.title,
        _description=row.description,
        _status=TaskStatus(row.status),
        _created_at=read_back_utc(row.created_at),
        _started_at=read_back_utc(row.started_at) if row.started_at is not None else None,
        _completed_at=read_back_utc(row.completed_at) if row.completed_at is not None else None,
        _failed_at=read_back_utc(row.failed_at) if row.failed_at is not None else None,
        _cancelled_at=read_back_utc(row.cancelled_at) if row.cancelled_at is not None else None,
        _failure=_failure_from_dict(row.failure),
    )


def conversation_to_row(conversation: Conversation) -> ConversationRow:
    return ConversationRow(
        id=str(conversation.id),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def conversation_from_row(row: ConversationRow) -> Conversation:
    return Conversation(
        _id=ConversationId.parse(row.id),
        _created_at=read_back_utc(row.created_at),
        _updated_at=read_back_utc(row.updated_at),
    )


def conversation_turn_to_row(turn: ConversationTurn) -> ConversationTurnRow:
    return ConversationTurnRow(
        id=str(turn.id),
        conversation_id=str(turn.conversation_id),
        client_turn_id=turn.client_turn_id,
        input_text=turn.input_text,
        input_mode=turn.input_mode.value,
        recognition_language=turn.recognition_language,
        task_id=str(turn.task_id),
        run_id=str(turn.run_id),
        created_at=turn.created_at,
    )


def conversation_turn_from_row(row: ConversationTurnRow) -> ConversationTurn:
    return ConversationTurn(
        id=ConversationTurnId.parse(row.id),
        conversation_id=ConversationId.parse(row.conversation_id),
        client_turn_id=row.client_turn_id,
        input_text=row.input_text,
        input_mode=ConversationInputMode(row.input_mode),
        recognition_language=row.recognition_language,
        task_id=TaskId.parse(row.task_id),
        run_id=RunId.parse(row.run_id),
        created_at=read_back_utc(row.created_at),
    )


def task_event_to_row(event: TaskEvent) -> TaskEventRow:
    return TaskEventRow(
        id=str(event.id),
        task_id=str(event.task_id),
        type=event.type.value,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )


def task_event_from_row(row: TaskEventRow) -> TaskEvent:
    return TaskEvent(
        id=TaskEventId.parse(row.id),
        task_id=TaskId.parse(row.task_id),
        type=TaskEventType(row.type),
        sequence=row.sequence,
        occurred_at=read_back_utc(row.occurred_at),
        payload=cast(JsonValue, row.payload),
    )


def run_to_row(run: Run) -> RunRow:
    return RunRow(
        id=str(run.id),
        task_id=str(run.task_id),
        execution_id=str(run.execution_id),
        status=run.status.value,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        failure=_failure_to_dict(run.failure),
        approval_request_id=str(run.approval_request_id) if run.approval_request_id else None,
    )


def run_from_row(row: RunRow) -> Run:
    return Run(
        _id=RunId.parse(row.id),
        _task_id=TaskId.parse(row.task_id),
        _execution_id=RunId.parse(row.execution_id),
        _status=RunStatus(row.status),
        _created_at=read_back_utc(row.created_at),
        _started_at=read_back_utc(row.started_at) if row.started_at is not None else None,
        _ended_at=read_back_utc(row.ended_at) if row.ended_at is not None else None,
        _failure=_failure_from_dict(row.failure),
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def schedule_to_row(schedule: Schedule) -> ScheduleRow:
    return ScheduleRow(
        id=str(schedule.id),
        task_id=str(schedule.task_id),
        kind=schedule.kind.value,
        cron=schedule.cron,
        run_at=schedule.run_at,
        timezone=schedule.timezone,
        status=schedule.status.value,
        next_fire_at=schedule.next_fire_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def schedule_from_row(row: ScheduleRow) -> Schedule:
    return Schedule(
        _id=ScheduleId.parse(row.id),
        _task_id=TaskId.parse(row.task_id),
        _kind=ScheduleKind(row.kind),
        _cron=row.cron,
        _run_at=read_back_utc(row.run_at) if row.run_at else None,
        _timezone=row.timezone,
        _status=ScheduleStatus(row.status),
        _next_fire_at=read_back_utc(row.next_fire_at) if row.next_fire_at else None,
        _created_at=read_back_utc(row.created_at),
        _updated_at=read_back_utc(row.updated_at),
    )


def schedule_fire_to_row(fire: ScheduleFire) -> ScheduleFireRow:
    return ScheduleFireRow(
        id=str(fire.id),
        schedule_id=str(fire.schedule_id),
        scheduled_for=fire.scheduled_for,
        fired_at=fire.fired_at,
        run_id=str(fire.run_id),
    )


def schedule_fire_from_row(row: ScheduleFireRow) -> ScheduleFire:
    return ScheduleFire(
        id=ScheduleFireId.parse(row.id),
        schedule_id=ScheduleId.parse(row.schedule_id),
        scheduled_for=read_back_utc(row.scheduled_for),
        fired_at=read_back_utc(row.fired_at),
        run_id=RunId.parse(row.run_id),
    )


def schedule_delivery_policy_to_row(policy: ScheduleDeliveryPolicy) -> ScheduleDeliveryPolicyRow:
    return ScheduleDeliveryPolicyRow(
        schedule_id=str(policy.schedule_id),
        route_id=policy.route_id,
        enabled=policy.enabled,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def schedule_delivery_policy_from_row(row: ScheduleDeliveryPolicyRow) -> ScheduleDeliveryPolicy:
    return ScheduleDeliveryPolicy.reconstruct(
        schedule_id=ScheduleId.parse(row.schedule_id),
        route_id=row.route_id,
        enabled=row.enabled,
        created_at=read_back_utc(row.created_at),
        updated_at=read_back_utc(row.updated_at),
    )


def schedule_fire_delivery_plan_to_row(
    plan: ScheduleFireDeliveryPlan,
) -> ScheduleFireDeliveryPlanRow:
    return ScheduleFireDeliveryPlanRow(
        id=str(plan.id),
        schedule_fire_id=str(plan.schedule_fire_id),
        schedule_id=str(plan.schedule_id),
        execution_id=str(plan.execution_id),
        route_id=plan.route_id,
        route_fingerprint=plan.route_fingerprint,
        route_max_body_chars=plan.route_max_body_chars,
        content_source=plan.content_source.value,
        status=plan.status.value,
        reason_code=plan.reason_code,
        content_rejected_run_id=(
            str(plan.content_rejected_run_id) if plan.content_rejected_run_id is not None else None
        ),
        created_at=plan.created_at,
    )


def schedule_fire_delivery_plan_from_row(
    row: ScheduleFireDeliveryPlanRow,
) -> ScheduleFireDeliveryPlan:
    return ScheduleFireDeliveryPlan(
        ScheduleFireDeliveryPlanId.parse(row.id),
        ScheduleFireId.parse(row.schedule_fire_id),
        ScheduleId.parse(row.schedule_id),
        RunId.parse(row.execution_id),
        row.route_id,
        row.route_fingerprint,
        row.route_max_body_chars,
        ScheduleFireDeliveryContentSource(row.content_source),
        ScheduleFireDeliveryPlanStatus(row.status),
        row.reason_code,
        (
            RunId.parse(row.content_rejected_run_id)
            if row.content_rejected_run_id is not None
            else None
        ),
        read_back_utc(row.created_at),
    )


def run_step_to_row(step: RunStep) -> RunStepRow:
    return RunStepRow(
        id=str(step.id),
        run_id=str(step.run_id),
        name=step.name,
        position=step.position,
        status=step.status.value,
        created_at=step.created_at,
        started_at=step.started_at,
        ended_at=step.ended_at,
        failure=_failure_to_dict(step.failure),
        approval_request_id=str(step.approval_request_id) if step.approval_request_id else None,
    )


def run_step_from_row(row: RunStepRow) -> RunStep:
    return RunStep(
        _id=RunStepId.parse(row.id),
        _run_id=RunId.parse(row.run_id),
        _name=row.name,
        _position=row.position,
        _status=RunStepStatus(row.status),
        _created_at=read_back_utc(row.created_at),
        _started_at=read_back_utc(row.started_at) if row.started_at is not None else None,
        _ended_at=read_back_utc(row.ended_at) if row.ended_at is not None else None,
        _failure=_failure_from_dict(row.failure),
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
    )


def approval_to_row(approval: ApprovalRequest) -> ApprovalRequestRow:
    return ApprovalRequestRow(
        id=str(approval.id),
        run_id=str(approval.run_id) if approval.run_id is not None else None,
        step_id=str(approval.step_id) if approval.step_id else None,
        category=approval.category.value,
        summary=approval.summary,
        reason=approval.reason,
        requested_action=approval.requested_action,
        requested_input=approval.requested_input,
        status=approval.status.value,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
        resolved_at=approval.resolved_at,
        resolution_note=approval.resolution_note,
        resolver=approval.resolver,
        authorization_fingerprint=approval.authorization_fingerprint,
        consumed_at=approval.consumed_at,
        subject_kind=approval.subject_kind.value,
        subject_id=(approval.subject_id if approval.run_id is None else None),
    )


def approval_from_row(row: ApprovalRequestRow) -> ApprovalRequest:
    return ApprovalRequest(
        _id=ApprovalRequestId.parse(row.id),
        _run_id=RunId.parse(row.run_id) if row.run_id else None,
        _step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        _category=ApprovalCategory(row.category),
        _summary=row.summary,
        _reason=row.reason,
        _requested_action=row.requested_action,
        _requested_input=cast(JsonValue, row.requested_input),
        _status=ApprovalStatus(row.status),
        _requested_at=read_back_utc(row.requested_at),
        _expires_at=read_back_utc(row.expires_at) if row.expires_at is not None else None,
        _resolved_at=read_back_utc(row.resolved_at) if row.resolved_at is not None else None,
        _resolution_note=row.resolution_note,
        _resolver=row.resolver,
        _authorization_fingerprint=row.authorization_fingerprint,
        _consumed_at=read_back_utc(row.consumed_at) if row.consumed_at is not None else None,
        _subject_kind=ApprovalSubjectKind(row.subject_kind),
        _subject_id=row.subject_id,
    )


def artifact_to_row(artifact: Artifact) -> ArtifactRow:
    return ArtifactRow(
        id=str(artifact.id),
        run_id=str(artifact.run_id),
        step_id=str(artifact.step_id) if artifact.step_id else None,
        kind=artifact.kind.value,
        name=artifact.name,
        media_type=artifact.media_type,
        location=artifact.location,
        created_at=artifact.created_at,
        size=artifact.size,
        checksum=artifact.checksum,
        artifact_metadata=artifact.metadata,
    )


def artifact_from_row(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=ArtifactId.parse(row.id),
        run_id=RunId.parse(row.run_id),
        step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        kind=ArtifactKind(row.kind),
        name=row.name,
        media_type=row.media_type,
        location=row.location,
        created_at=read_back_utc(row.created_at),
        size=row.size,
        checksum=row.checksum,
        metadata=cast(JsonValue, row.artifact_metadata),
    )


def tool_invocation_to_row(invocation: ToolInvocation) -> ToolInvocationRow:
    provenance = invocation.provenance
    return ToolInvocationRow(
        id=str(invocation.id),
        run_id=str(invocation.run_id),
        step_id=str(invocation.step_id) if invocation.step_id else None,
        approval_request_id=str(invocation.approval_request_id)
        if invocation.approval_request_id
        else None,
        tool_name=invocation.tool_name,
        requested_input=invocation.requested_input,
        status=invocation.status.value,
        requested_at=invocation.requested_at,
        started_at=invocation.started_at,
        completed_at=invocation.completed_at,
        output=invocation.output,
        output_set=invocation.output_set,
        failure=_failure_to_dict(invocation.failure),
        provenance_kind=provenance.kind if provenance is not None else None,
        provenance_target=provenance.target if provenance is not None else None,
        provenance_remote_name=provenance.remote_name if provenance is not None else None,
        provenance_binding_fingerprint=(
            provenance.binding_fingerprint if provenance is not None else None
        ),
    )


def tool_invocation_from_row(row: ToolInvocationRow) -> ToolInvocation:
    return ToolInvocation(
        _id=ToolInvocationId.parse(row.id),
        _run_id=RunId.parse(row.run_id),
        _step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        _approval_request_id=ApprovalRequestId.parse(row.approval_request_id)
        if row.approval_request_id
        else None,
        _tool_name=row.tool_name,
        _requested_input=cast(JsonValue, row.requested_input),
        _status=ToolInvocationStatus(row.status),
        _requested_at=read_back_utc(row.requested_at),
        _started_at=read_back_utc(row.started_at) if row.started_at is not None else None,
        _completed_at=read_back_utc(row.completed_at) if row.completed_at is not None else None,
        _output=cast(JsonValue, row.output),
        _output_set=row.output_set,
        _failure=_failure_from_dict(row.failure),
        _provenance=_provenance_from_row(row),
    )


def outbound_delivery_to_row(delivery: OutboundDelivery) -> OutboundDeliveryRow:
    return OutboundDeliveryRow(
        id=str(delivery.id),
        source_kind=delivery.source_kind.value,
        source_run_id=str(delivery.source_run_id),
        source_tool_invocation_id=(
            str(delivery.source_tool_invocation_id) if delivery.source_tool_invocation_id else None
        ),
        source_schedule_fire_id=(
            str(delivery.source_schedule_fire_id) if delivery.source_schedule_fire_id else None
        ),
        route_id=delivery.route_id,
        route_fingerprint=delivery.route_fingerprint,
        subject=delivery.subject,
        body=delivery.body,
        body_sha256=delivery.body_sha256,
        status=delivery.status.value,
        available_at=delivery.available_at,
        attempt_count=delivery.attempt_count,
        claim_owner=delivery.claim_owner,
        claim_token=delivery.claim_token,
        claim_generation=delivery.claim_generation,
        claim_expires_at=delivery.claim_expires_at,
        provider_message_id=delivery.provider_message_id,
        failure_code=delivery.failure_code,
        failure_message=delivery.failure_message,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
        delivered_at=delivery.delivered_at,
        dispatch_started_at=delivery.dispatch_started_at,
    )


def outbound_delivery_from_row(row: OutboundDeliveryRow) -> OutboundDelivery:
    return OutboundDelivery(
        id=DeliveryId.parse(row.id),
        source_kind=DeliverySourceKind(row.source_kind),
        source_run_id=RunId.parse(row.source_run_id),
        source_tool_invocation_id=(
            ToolInvocationId.parse(row.source_tool_invocation_id)
            if row.source_tool_invocation_id
            else None
        ),
        source_schedule_fire_id=(
            ScheduleFireId.parse(row.source_schedule_fire_id)
            if row.source_schedule_fire_id
            else None
        ),
        route_id=row.route_id,
        route_fingerprint=row.route_fingerprint,
        subject=row.subject,
        body=row.body,
        body_sha256=row.body_sha256,
        status=DeliveryStatus(row.status),
        available_at=read_back_utc(row.available_at),
        attempt_count=row.attempt_count,
        claim_owner=row.claim_owner,
        claim_token=row.claim_token,
        claim_generation=row.claim_generation,
        claim_expires_at=(read_back_utc(row.claim_expires_at) if row.claim_expires_at else None),
        provider_message_id=row.provider_message_id,
        failure_code=row.failure_code,
        failure_message=row.failure_message,
        created_at=read_back_utc(row.created_at),
        updated_at=read_back_utc(row.updated_at),
        delivered_at=read_back_utc(row.delivered_at) if row.delivered_at else None,
        dispatch_started_at=(
            read_back_utc(row.dispatch_started_at) if row.dispatch_started_at else None
        ),
    )


def delivery_attempt_from_row(row: DeliveryAttemptRow) -> DeliveryAttempt:
    """Reconstruct a persisted attempt, terminal rows included.

    There is deliberately no `delivery_attempt_to_row`: attempts are only ever
    written by the repository's claim-fenced SQL, so an ORM insert helper would
    be exactly the generic bypass this ledger must not have. Reconstruction
    still runs the full constructor, so a row whose shape violates the domain
    invariants fails loudly on read instead of being silently trusted.
    """
    return DeliveryAttempt(
        DeliveryAttemptId.parse(row.id),
        DeliveryId.parse(row.delivery_id),
        row.claim_generation,
        read_back_utc(row.started_at),
        read_back_utc(row.finished_at) if row.finished_at else None,
        DeliveryAttemptOutcome(row.outcome),
        row.failure_code,
    )


def _provenance_from_row(row: ToolInvocationRow) -> ToolProvenance | None:
    values = (
        row.provenance_kind,
        row.provenance_target,
        row.provenance_remote_name,
        row.provenance_binding_fingerprint,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError(f"tool invocation {row.id} has partial provenance columns")
    kind, target, remote_name, fingerprint = values
    assert kind is not None and target is not None
    assert remote_name is not None and fingerprint is not None
    return ToolProvenance(
        kind=kind,
        target=target,
        remote_name=remote_name,
        binding_fingerprint=fingerprint,
    )


def run_event_to_row(event: RunEvent) -> RunEventRow:
    return RunEventRow(
        id=str(event.id),
        run_id=str(event.run_id),
        step_id=str(event.step_id) if event.step_id else None,
        type=event.type.value,
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        payload=event.payload,
    )


def run_event_from_row(row: RunEventRow) -> RunEvent:
    return RunEvent(
        id=RunEventId.parse(row.id),
        run_id=RunId.parse(row.run_id),
        step_id=RunStepId.parse(row.step_id) if row.step_id else None,
        type=RunEventType(row.type),
        sequence=row.sequence,
        occurred_at=read_back_utc(row.occurred_at),
        payload=cast(JsonValue, row.payload),
    )


def run_work_item_from_row(row: RunWorkItemRow) -> RunWorkItemView:
    return RunWorkItemView(
        run_id=RunId.parse(row.run_id),
        available_at=read_back_utc(row.available_at),
        enqueued_at=read_back_utc(row.enqueued_at),
        claimed_by=row.claimed_by,
        claim_token=row.claim_token,
        claim_generation=row.claim_generation,
        claimed_at=read_back_utc(row.claimed_at) if row.claimed_at is not None else None,
        heartbeat_at=read_back_utc(row.heartbeat_at) if row.heartbeat_at is not None else None,
        lease_expires_at=read_back_utc(row.lease_expires_at)
        if row.lease_expires_at is not None
        else None,
    )


def index_snapshot_to_row(snapshot: IndexSnapshot) -> MemoryIndexSnapshotRow:
    return MemoryIndexSnapshotRow(
        id=snapshot.id,
        vault_identity_hash=snapshot.vault_identity_hash,
        source_snapshot_hash=snapshot.source_snapshot_hash,
        graph_checksum=snapshot.graph_checksum,
        graphify_version=snapshot.graphify_version,
        status=snapshot.state.value,
        built_at=snapshot.built_at,
        file_count=snapshot.file_count,
        node_count=snapshot.node_count,
        edge_count=snapshot.edge_count,
        failure_code=snapshot.failure_code,
    )


def index_snapshot_from_row(row: MemoryIndexSnapshotRow) -> IndexSnapshot:
    # ponytail: build_duration_seconds/source_total_bytes are outside the
    # audited schema (.herdr/phase12-invariants.md smallest-schema-addition);
    # zeroed on read-back. Add columns if a caller needs them.
    return IndexSnapshot(
        id=row.id,
        vault_identity_hash=row.vault_identity_hash,
        source_snapshot_hash=row.source_snapshot_hash,
        graph_checksum=row.graph_checksum,
        graphify_version=row.graphify_version,
        state=IndexState(row.status),
        built_at=read_back_utc(row.built_at),
        build_duration_seconds=0.0,
        file_count=row.file_count,
        source_total_bytes=0,
        node_count=row.node_count,
        edge_count=row.edge_count,
        failure_code=row.failure_code,
    )


def memory_retrieval_record_to_row(record: MemoryRetrievalRecord) -> MemoryRetrievalRecordRow:
    return MemoryRetrievalRecordRow(
        id=record.id,
        run_id=str(record.run_id),
        turn_number=record.turn_number,
        query_hash=record.query_hash,
        source_snapshot_id=record.source_snapshot_id,
        index_snapshot_id=record.index_snapshot_id,
        created_at=record.created_at,
        candidate_count=record.candidate_count,
        selected_count=record.selected_count,
    )


def memory_retrieval_record_from_row(
    row: MemoryRetrievalRecordRow, items: tuple[MemoryRetrievalItem, ...]
) -> MemoryRetrievalRecord:
    return MemoryRetrievalRecord(
        id=row.id,
        run_id=RunId.parse(row.run_id),
        turn_number=row.turn_number,
        query_hash=row.query_hash,
        source_snapshot_id=row.source_snapshot_id,
        index_snapshot_id=row.index_snapshot_id,
        created_at=read_back_utc(row.created_at),
        candidate_count=row.candidate_count,
        selected_count=row.selected_count,
        items=items,
    )


def memory_retrieval_item_to_row(
    item: MemoryRetrievalItem, *, record_id: str
) -> MemoryRetrievalItemRow:
    return MemoryRetrievalItemRow(
        id=f"{record_id}:{item.rank}",
        record_id=record_id,
        path=item.path,
        heading=item.heading,
        start_line=item.start_line,
        end_line=item.end_line,
        content_hash=item.content_hash,
        rank=item.rank,
        methods=[method.value for method in item.methods],
        truncated=item.truncated,
    )


def memory_retrieval_item_from_row(row: MemoryRetrievalItemRow) -> MemoryRetrievalItem:
    return MemoryRetrievalItem(
        path=row.path,
        heading=row.heading,
        start_line=row.start_line,
        end_line=row.end_line,
        content_hash=row.content_hash,
        rank=row.rank,
        methods=tuple(RetrievalMethod(value) for value in cast(list[str], row.methods)),
        truncated=row.truncated,
    )

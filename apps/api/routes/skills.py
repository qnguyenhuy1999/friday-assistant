from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    page_from_query,
)
from apps.api.schemas.skills import (
    CandidateEvaluationResponse,
    CreateEvaluationSuiteBody,
    CreateSkillBody,
    CreateSkillRevisionBody,
    EvaluateImprovementProposalBody,
    EvaluationCaseResponse,
    EvaluationCaseResultResponse,
    EvaluationRunResponse,
    EvaluationSuiteResponse,
    ImprovementProposalResponse,
    RequestRollbackBody,
    ResolveSkillRequestBody,
    RunEvaluationBody,
    SkillEvidenceSnapshotResponse,
    SkillImprovementPolicyBody,
    SkillImprovementPolicyResponse,
    SkillPageResponse,
    SkillPromotionResponse,
    SkillResponse,
    SkillRevisionResponse,
    SkillRollbackResponse,
    SkillUsageRecordResponse,
)
from friday.application.errors import (
    SkillEvaluationRunNotFound,
    SkillEvaluationSuiteNotFound,
    SkillImprovementProposalNotFound,
    SkillNotFound,
    SkillPromotionRequestNotFound,
    SkillRollbackRequestNotFound,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.skill_evaluation import (
    CompareSkillImprovementProposal,
    CreateSkillEvaluationSuite,
    DeterministicEvaluatorRegistry,
    RunSkillEvaluation,
)
from friday.application.skill_improvement import (
    CancelSkillImprovementProposal,
)
from friday.application.skill_improvement_policy import (
    RunSkillImprovementPolicyNow,
    SaveSkillImprovementPolicy,
)
from friday.application.skill_promotion import (
    CancelSkillPromotion,
    CancelSkillRollback,
    ExecuteSkillPromotion,
    ExecuteSkillRollback,
    RejectSkillPromotion,
    RejectSkillRollback,
    RequestSkillPromotion,
    RequestSkillRollback,
)
from friday.application.skill_registry import (
    ActivateSkillRevision,
    ArchiveSkill,
    CreateSkill,
    CreateSkillRevision,
    DisableSkill,
    GetSkill,
    ListSkills,
)
from friday.domain import (
    Skill,
    SkillEvaluationRunId,
    SkillEvaluationSuiteId,
    SkillId,
    SkillImprovementPolicy,
    SkillImprovementProposal,
    SkillImprovementProposalId,
    SkillPromotionRequest,
    SkillPromotionRequestId,
    SkillRevision,
    SkillRevisionId,
    SkillRevisionSourceKind,
    SkillRollbackRequestId,
)
from friday.domain.identifiers import SkillEvidenceSnapshotId

router = APIRouter(prefix="/v1/skills", tags=["skills"])
Uow = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDep = Annotated[Clock, Depends(get_clock)]


def _skill(x: Skill) -> SkillResponse:
    return SkillResponse(
        id=str(x.id),
        key=x.key,
        display_name=x.display_name,
        description=x.description,
        status=x.status.value,
        active_revision_id=str(x.active_revision_id) if x.active_revision_id else None,
        created_at=x.created_at,
        updated_at=x.updated_at,
    )


def _revision(x: SkillRevision) -> SkillRevisionResponse:
    return SkillRevisionResponse(
        id=str(x.id),
        skill_id=str(x.skill_id),
        version=x.version,
        instructions=x.instructions,
        content_sha256=x.content_sha256,
        source_kind=x.source_kind.value,
        created_at=x.created_at,
    )


def _proposal(x: SkillImprovementProposal) -> ImprovementProposalResponse:
    return ImprovementProposalResponse(
        id=str(x.id),
        skill_id=str(x.skill_id),
        base_revision_id=str(x.base_revision_id),
        status=x.status.value,
        evidence_snapshot_id=str(x.evidence_snapshot_id),
        evidence_snapshot_hash=x.evidence_snapshot_hash,
        proposed_instructions=x.proposed_instructions,
        proposed_content_sha256=x.proposed_content_sha256,
        rationale=x.rationale,
        generator_version=x.generator_version,
        created_at=x.created_at,
    )


def _candidate_evaluation(x: object) -> CandidateEvaluationResponse:
    from friday.domain import SkillCandidateEvaluation

    value = x if isinstance(x, SkillCandidateEvaluation) else None
    assert value is not None
    return CandidateEvaluationResponse(
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
        comparison_report=value.comparison_report,
    )


def _promotion(
    x: SkillPromotionRequest,
    *,
    proposal: SkillImprovementProposal | None = None,
    comparison: object | None = None,
) -> SkillPromotionResponse:
    from friday.domain import SkillCandidateEvaluation

    candidate = comparison if isinstance(comparison, SkillCandidateEvaluation) else None
    return SkillPromotionResponse(
        id=str(x.id),
        proposal_id=str(x.proposal_id),
        skill_id=str(x.skill_id),
        status=x.status.value,
        authorization_fingerprint=x.authorization_fingerprint,
        target_version=x.target_version,
        promoted_revision_id=str(x.promoted_revision_id) if x.promoted_revision_id else None,
        approval_request_id=str(x.approval_request_id) if x.approval_request_id else None,
        candidate_instructions=proposal.proposed_instructions if proposal is not None else "",
        candidate_sha256=x.candidate_sha256,
        evidence_snapshot_id=str(proposal.evidence_snapshot_id) if proposal is not None else "",
        evidence_snapshot_hash=proposal.evidence_snapshot_hash if proposal is not None else "",
        comparison_report=candidate.comparison_report if candidate is not None else {},
        recommendation=candidate.recommendation.value if candidate is not None else "",
    )


def _rollback(x: object) -> SkillRollbackResponse:
    from friday.domain import SkillRollbackRequest

    value = x if isinstance(x, SkillRollbackRequest) else None
    assert value is not None
    return SkillRollbackResponse(
        id=str(value.id),
        skill_id=str(value.skill_id),
        expected_current_revision_id=str(value.expected_current_revision_id),
        target_revision_id=str(value.target_revision_id),
        reason=value.reason,
        status=value.status.value,
        authorization_fingerprint=value.authorization_fingerprint,
        approval_request_id=str(value.approval_request_id) if value.approval_request_id else None,
        resolved_at=value.resolved_at,
        resolver=value.resolver,
    )


def _policy(x: SkillImprovementPolicy) -> SkillImprovementPolicyResponse:
    return SkillImprovementPolicyResponse(
        skill_id=str(x.skill_id),
        enabled=x.enabled,
        minimum_usage_records=x.minimum_usage_records,
        minimum_failures=x.minimum_failures,
        minimum_harmful_feedback=x.minimum_harmful_feedback,
        evaluation_suite_id=str(x.evaluation_suite_id),
        cooldown_seconds=x.cooldown_seconds,
        max_open_proposals=x.max_open_proposals,
        evidence_window_size=x.evidence_window_size,
        generator_version=x.generator_version,
        comparison_policy_version=x.comparison_policy_version,
        last_triggered_at=x.last_triggered_at,
    )


def _promotion_response(
    uow_factory: UnitOfWorkFactory, request: SkillPromotionRequest
) -> SkillPromotionResponse:
    with uow_factory() as uow:
        proposal = uow.skill_improvement_proposals.get(request.proposal_id)
        comparison = uow.skill_candidate_evaluations.get_for_proposal(request.proposal_id)
        return _promotion(request, proposal=proposal, comparison=comparison)


def _evaluation_run_response(uow_factory: UnitOfWorkFactory, run: object) -> EvaluationRunResponse:
    from friday.domain import SkillEvaluationRun

    value = run if isinstance(run, SkillEvaluationRun) else None
    assert value is not None
    with uow_factory() as uow:
        return EvaluationRunResponse(
            id=str(value.id),
            suite_id=str(value.suite_id),
            skill_id=str(value.skill_id),
            revision_id=str(value.revision_id) if value.revision_id else None,
            proposal_id=str(value.proposal_id) if value.proposal_id else None,
            status=value.status.value,
            aggregate_result=value.aggregate_result,
            runtime_fingerprint=value.runtime_fingerprint,
            target_content_sha256=value.target_content_sha256,
            runtime_metadata=value.runtime_metadata,
            suite_snapshot=value.suite_snapshot,
            case_results=[
                EvaluationCaseResultResponse(
                    evaluation_run_id=str(result.evaluation_run_id),
                    case_id=str(result.case_id),
                    status=result.status.value,
                    score=result.score,
                    reason_code=result.reason_code,
                    bounded_details=result.bounded_details,
                    output_sha256=result.output_sha256,
                )
                for result in uow.skill_evaluation_case_results.list_for_run(value.id)
            ],
        )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSkill",
)
def create(body: CreateSkillBody, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(
        CreateSkill(uow, clock).execute(
            key=body.key, display_name=body.display_name, description=body.description
        )
    )


@router.get("", response_model=SkillPageResponse, operation_id="listSkills")
def list_skills(
    uow: Uow,
    # Keep the legacy no-parameter response size while allowing the operator
    # UI and new callers to opt into smaller cursor pages explicitly.
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = MAX_PAGE_SIZE,
    cursor: str | None = None,
) -> SkillPageResponse:
    after = decode_cursor(
        cursor,
        collection="skills",
        parent_id=None,
        order="created_at_id_asc",
        parts=2,
    )
    results = ListSkills(uow).page(
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="skills",
        parent_id=None,
        order="created_at_id_asc",
        key=lambda skill: (skill.created_at.isoformat(), str(skill.id)),
    )
    return SkillPageResponse(items=[_skill(x) for x in page], next_cursor=next_cursor)


@router.get("/{skill_id}", response_model=SkillResponse, operation_id="getSkill")
def get_skill(skill_id: UUID, uow: Uow) -> SkillResponse:
    return _skill(GetSkill(uow).execute(SkillId.parse(str(skill_id))))


@router.post(
    "/{skill_id}/revisions",
    response_model=SkillRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSkillRevision",
)
def create_revision(
    skill_id: UUID, body: CreateSkillRevisionBody, uow: Uow, clock: ClockDep
) -> SkillRevisionResponse:
    return _revision(
        CreateSkillRevision(uow, clock).execute(
            skill_id=SkillId.parse(str(skill_id)),
            instructions=body.instructions,
            source_kind=SkillRevisionSourceKind(body.source_kind),
        )
    )


@router.get(
    "/{skill_id}/revisions",
    response_model=list[SkillRevisionResponse],
    operation_id="listSkillRevisions",
)
def list_revisions(
    skill_id: UUID,
    uow: Uow,
    limit: Annotated[int | None, Query(ge=1, le=MAX_PAGE_SIZE)] = None,
    before_version: Annotated[int | None, Query(ge=1)] = None,
) -> list[SkillRevisionResponse]:
    typed_skill_id = SkillId.parse(str(skill_id))
    getter = GetSkill(uow)
    if limit is None and before_version is None:
        revisions = getter.list_revisions(typed_skill_id)
    else:
        revisions = getter.list_revisions_page(
            typed_skill_id,
            limit or DEFAULT_PAGE_SIZE,
            before_version,
        )
    return [_revision(x) for x in revisions]


@router.get(
    "/{skill_id}/revisions/{revision_id}",
    response_model=SkillRevisionResponse,
    operation_id="getSkillRevision",
)
def get_revision(skill_id: UUID, revision_id: UUID, uow: Uow) -> SkillRevisionResponse:
    return _revision(
        GetSkill(uow).get_revision(
            SkillId.parse(str(skill_id)),
            SkillRevisionId.parse(str(revision_id)),
        )
    )


@router.post(
    "/{skill_id}/revisions/{revision_id}/activate",
    response_model=SkillResponse,
    operation_id="activateSkillRevision",
)
def activate(skill_id: UUID, revision_id: UUID, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(
        ActivateSkillRevision(uow, clock).execute(
            skill_id=SkillId.parse(str(skill_id)),
            revision_id=SkillRevisionId.parse(str(revision_id)),
        )
    )


@router.post("/{skill_id}/disable", response_model=SkillResponse, operation_id="disableSkill")
def disable(skill_id: UUID, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(DisableSkill(uow, clock).execute(SkillId.parse(str(skill_id))))


@router.post("/{skill_id}/archive", response_model=SkillResponse, operation_id="archiveSkill")
def archive(skill_id: UUID, uow: Uow, clock: ClockDep) -> SkillResponse:
    return _skill(ArchiveSkill(uow, clock).execute(SkillId.parse(str(skill_id))))


@router.get(
    "/{skill_id}/usage",
    response_model=list[SkillUsageRecordResponse],
    operation_id="listSkillUsage",
)
def list_usage(skill_id: UUID, uow: Uow) -> list[SkillUsageRecordResponse]:
    with uow() as tx:
        typed_skill_id = SkillId.parse(str(skill_id))
        if tx.skills.get(typed_skill_id) is None:
            raise SkillNotFound(typed_skill_id)
        return [
            SkillUsageRecordResponse(
                id=str(x.id),
                run_id=str(x.run_id),
                task_id=str(x.task_id),
                skill_id=str(x.skill_id),
                revision_id=str(x.revision_id),
                position=x.position,
                resolution_id=x.resolution_id,
                execution_id=str(x.execution_id),
                attempt_number=x.attempt_number,
                started_at=x.started_at,
                outcome=x.outcome.value,
                failure_code=x.failure_code,
                tool_call_count=x.tool_call_count,
                approval_count=x.approval_count,
                duration_ms=x.duration_ms,
                completed_at=x.completed_at,
                created_at=x.created_at,
            )
            for x in tx.skill_usage_records.list_for_skill(typed_skill_id, 100)
        ]


@router.get(
    "/evidence-snapshots/{snapshot_id}",
    response_model=SkillEvidenceSnapshotResponse,
    operation_id="getSkillEvidenceSnapshot",
)
def get_evidence_snapshot(snapshot_id: UUID, uow: Uow) -> SkillEvidenceSnapshotResponse:
    with uow() as tx:
        snapshot = tx.skill_evidence_snapshots.get(SkillEvidenceSnapshotId.parse(str(snapshot_id)))
        if snapshot is None:
            from friday.application.errors import SkillEvidenceSnapshotNotFound

            raise SkillEvidenceSnapshotNotFound(snapshot_id)
        return SkillEvidenceSnapshotResponse(
            id=str(snapshot.id),
            skill_id=str(snapshot.skill_id),
            base_revision_id=str(snapshot.base_revision_id),
            content_sha256=snapshot.content_sha256,
            evidence=snapshot.evidence,
            created_at=snapshot.created_at,
        )


@router.get(
    "/{skill_id}/improvement-proposals",
    response_model=list[ImprovementProposalResponse],
    operation_id="listSkillImprovementProposals",
)
def list_improvement_proposals(skill_id: UUID, uow: Uow) -> list[ImprovementProposalResponse]:
    with uow() as tx:
        typed_skill_id = SkillId.parse(str(skill_id))
        if tx.skills.get(typed_skill_id) is None:
            raise SkillNotFound(typed_skill_id)
        proposals = tx.skill_improvement_proposals.list_for_skill(typed_skill_id)
        return [_proposal(x) for x in proposals]


@router.get(
    "/improvement-proposals/{proposal_id}",
    response_model=ImprovementProposalResponse,
    operation_id="getSkillImprovementProposal",
)
def get_improvement_proposal(proposal_id: UUID, uow: Uow) -> ImprovementProposalResponse:
    with uow() as tx:
        proposal = tx.skill_improvement_proposals.get(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
        if proposal is None:
            raise SkillImprovementProposalNotFound(proposal_id)
        return _proposal(proposal)


@router.post(
    "/improvement-proposals/{proposal_id}/cancel",
    response_model=ImprovementProposalResponse,
    operation_id="cancelSkillImprovementProposal",
)
def cancel_improvement_proposal(proposal_id: UUID, uow: Uow) -> ImprovementProposalResponse:
    return _proposal(
        CancelSkillImprovementProposal(uow).execute(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
    )


@router.post(
    "/improvement-proposals/{proposal_id}/evaluate",
    response_model=CandidateEvaluationResponse,
    operation_id="evaluateSkillImprovementProposal",
)
def evaluate_improvement_proposal(
    proposal_id: UUID, body: EvaluateImprovementProposalBody, uow: Uow, clock: ClockDep
) -> CandidateEvaluationResponse:
    comparison = CompareSkillImprovementProposal(
        uow, clock, DeterministicEvaluatorRegistry()
    ).execute(
        proposal_id=SkillImprovementProposalId.parse(str(proposal_id)),
        baseline_evaluation_run_id=SkillEvaluationRunId.parse(body.baseline_evaluation_run_id),
        candidate_outputs=body.candidate_outputs,
        comparison_policy_version=body.comparison_policy_version,
    )
    return _candidate_evaluation(comparison)


@router.get(
    "/improvement-proposals/{proposal_id}/evaluation",
    response_model=CandidateEvaluationResponse,
    operation_id="getSkillImprovementEvaluation",
)
def get_improvement_evaluation(proposal_id: UUID, uow: Uow) -> CandidateEvaluationResponse:
    with uow() as tx:
        comparison = tx.skill_candidate_evaluations.get_for_proposal(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
        if comparison is None:
            raise SkillImprovementProposalNotFound(proposal_id)
        return _candidate_evaluation(comparison)


@router.post(
    "/improvement-proposals/{proposal_id}/request-promotion",
    response_model=SkillPromotionResponse,
    operation_id="requestSkillPromotion",
)
def request_promotion(proposal_id: UUID, uow: Uow, clock: ClockDep) -> SkillPromotionResponse:
    request = RequestSkillPromotion(uow, clock).execute(
        SkillImprovementProposalId.parse(str(proposal_id))
    )
    return _promotion_response(uow, request)


@router.get(
    "/promotions/{promotion_id}",
    response_model=SkillPromotionResponse,
    operation_id="getSkillPromotionRequest",
)
def get_promotion(promotion_id: UUID, uow: Uow) -> SkillPromotionResponse:
    with uow() as tx:
        request = tx.skill_promotion_requests.get(SkillPromotionRequestId.parse(str(promotion_id)))
        if request is None:
            raise SkillPromotionRequestNotFound(promotion_id)
    return _promotion_response(uow, request)


@router.post(
    "/promotions/{promotion_id}/approve",
    response_model=SkillPromotionResponse,
    operation_id="executeSkillPromotion",
)
def approve_promotion(promotion_id: UUID, uow: Uow, clock: ClockDep) -> SkillPromotionResponse:
    request = ExecuteSkillPromotion(uow, clock).execute(
        SkillPromotionRequestId.parse(str(promotion_id))
    )
    return _promotion_response(uow, request)


@router.post(
    "/promotions/{promotion_id}/reject",
    response_model=SkillPromotionResponse,
    operation_id="rejectSkillPromotion",
)
def reject_promotion(
    promotion_id: UUID, body: ResolveSkillRequestBody, uow: Uow, clock: ClockDep
) -> SkillPromotionResponse:
    request = RejectSkillPromotion(uow, clock).execute(
        SkillPromotionRequestId.parse(str(promotion_id)), body.resolver
    )
    return _promotion_response(uow, request)


@router.post(
    "/promotions/{promotion_id}/cancel",
    response_model=SkillPromotionResponse,
    operation_id="cancelSkillPromotion",
)
def cancel_promotion(promotion_id: UUID, uow: Uow, clock: ClockDep) -> SkillPromotionResponse:
    request = CancelSkillPromotion(uow, clock).execute(
        SkillPromotionRequestId.parse(str(promotion_id))
    )
    return _promotion_response(uow, request)


@router.post(
    "/{skill_id}/request-rollback",
    response_model=SkillRollbackResponse,
    operation_id="requestSkillRollback",
)
def request_rollback(
    skill_id: UUID, body: RequestRollbackBody, uow: Uow, clock: ClockDep
) -> SkillRollbackResponse:
    request = RequestSkillRollback(uow, clock).execute(
        skill_id=SkillId.parse(str(skill_id)),
        target_revision_id=SkillRevisionId.parse(body.target_revision_id),
        reason=body.reason,
    )
    return _rollback(request)


@router.get(
    "/rollbacks/{rollback_id}",
    response_model=SkillRollbackResponse,
    operation_id="getSkillRollbackRequest",
)
def get_rollback(rollback_id: UUID, uow: Uow) -> SkillRollbackResponse:
    with uow() as tx:
        request = tx.skill_rollback_requests.get(SkillRollbackRequestId.parse(str(rollback_id)))
        if request is None:
            raise SkillRollbackRequestNotFound(rollback_id)
        return _rollback(request)


@router.post(
    "/rollbacks/{rollback_id}/approve",
    response_model=SkillRollbackResponse,
    operation_id="executeSkillRollback",
)
def approve_rollback(rollback_id: UUID, uow: Uow, clock: ClockDep) -> SkillRollbackResponse:
    request = ExecuteSkillRollback(uow, clock).execute(
        SkillRollbackRequestId.parse(str(rollback_id))
    )
    return _rollback(request)


@router.post(
    "/rollbacks/{rollback_id}/reject",
    response_model=SkillRollbackResponse,
    operation_id="rejectSkillRollback",
)
def reject_rollback(
    rollback_id: UUID, body: ResolveSkillRequestBody, uow: Uow, clock: ClockDep
) -> SkillRollbackResponse:
    return _rollback(
        RejectSkillRollback(uow, clock).execute(
            SkillRollbackRequestId.parse(str(rollback_id)), body.resolver
        )
    )


@router.post(
    "/rollbacks/{rollback_id}/cancel",
    response_model=SkillRollbackResponse,
    operation_id="cancelSkillRollback",
)
def cancel_rollback(rollback_id: UUID, uow: Uow, clock: ClockDep) -> SkillRollbackResponse:
    return _rollback(
        CancelSkillRollback(uow, clock).execute(SkillRollbackRequestId.parse(str(rollback_id)))
    )


@router.get(
    "/{skill_id}/improvement-policy",
    response_model=SkillImprovementPolicyResponse,
    operation_id="getSkillImprovementPolicy",
)
def get_improvement_policy(skill_id: UUID, uow: Uow) -> SkillImprovementPolicyResponse:
    with uow() as tx:
        policy = tx.skill_improvement_policies.get(SkillId.parse(str(skill_id)))
        if policy is None:
            raise SkillNotFound(SkillId.parse(str(skill_id)))
        return _policy(policy)


@router.put(
    "/{skill_id}/improvement-policy",
    response_model=SkillImprovementPolicyResponse,
    operation_id="putSkillImprovementPolicy",
)
def put_improvement_policy(
    skill_id: UUID, body: SkillImprovementPolicyBody, uow: Uow, clock: ClockDep
) -> SkillImprovementPolicyResponse:
    now = clock.now()
    policy = SkillImprovementPolicy(
        skill_id=SkillId.parse(str(skill_id)),
        enabled=body.enabled,
        minimum_usage_records=body.minimum_usage_records,
        minimum_failures=body.minimum_failures,
        minimum_harmful_feedback=body.minimum_harmful_feedback,
        evaluation_suite_id=SkillEvaluationSuiteId.parse(body.evaluation_suite_id),
        cooldown_seconds=body.cooldown_seconds,
        max_open_proposals=body.max_open_proposals,
        evidence_window_size=body.evidence_window_size,
        generator_version=body.generator_version,
        comparison_policy_version=body.comparison_policy_version,
        created_at=now,
        updated_at=now,
    )
    return _policy(SaveSkillImprovementPolicy(uow, clock).execute(policy))


@router.post(
    "/{skill_id}/improvement-policy/run-now",
    operation_id="runSkillImprovementPolicyNow",
)
def run_improvement_policy_now(skill_id: UUID, uow: Uow, clock: ClockDep) -> dict[str, bool]:
    return {"due": RunSkillImprovementPolicyNow(uow, clock).execute(SkillId.parse(str(skill_id)))}


@router.post(
    "/{skill_id}/evaluation-suites",
    response_model=EvaluationSuiteResponse,
    operation_id="createSkillEvaluationSuite",
)
def create_evaluation_suite(
    skill_id: UUID, body: CreateEvaluationSuiteBody, uow: Uow, clock: ClockDep
) -> EvaluationSuiteResponse:
    suite = CreateSkillEvaluationSuite(uow, clock).execute(
        skill_id=SkillId.parse(str(skill_id)),
        name=body.name,
        description=body.description,
        cases=[(x.input, x.expected_properties, x.grading_kind) for x in body.cases],
    )
    with uow() as tx:
        return EvaluationSuiteResponse(
            id=str(suite.id),
            skill_id=str(suite.skill_id),
            name=suite.name,
            description=suite.description,
            status=suite.status.value,
            created_at=suite.created_at,
            updated_at=suite.updated_at,
            cases=[
                EvaluationCaseResponse(
                    id=str(case.id),
                    suite_id=str(case.suite_id),
                    position=case.position,
                    input=case.input,
                    expected_properties=case.expected_properties,
                    grading_kind=case.grading_kind,
                    created_at=case.created_at,
                    updated_at=case.updated_at,
                )
                for case in tx.skill_evaluation_cases.list_for_suite(suite.id)
            ],
        )


@router.get(
    "/{skill_id}/evaluation-suites",
    response_model=list[EvaluationSuiteResponse],
    operation_id="listSkillEvaluationSuites",
)
def list_evaluation_suites(skill_id: UUID, uow: Uow) -> list[EvaluationSuiteResponse]:
    with uow() as tx:
        typed_skill_id = SkillId.parse(str(skill_id))
        if tx.skills.get(typed_skill_id) is None:
            raise SkillNotFound(typed_skill_id)
        return [
            EvaluationSuiteResponse(
                id=str(x.id),
                skill_id=str(x.skill_id),
                name=x.name,
                description=x.description,
                status=x.status.value,
                created_at=x.created_at,
                updated_at=x.updated_at,
                cases=[
                    EvaluationCaseResponse(
                        id=str(case.id),
                        suite_id=str(case.suite_id),
                        position=case.position,
                        input=case.input,
                        expected_properties=case.expected_properties,
                        grading_kind=case.grading_kind,
                        created_at=case.created_at,
                        updated_at=case.updated_at,
                    )
                    for case in tx.skill_evaluation_cases.list_for_suite(x.id)
                ],
            )
            for x in tx.skill_evaluation_suites.list_for_skill(typed_skill_id)
        ]


@router.get(
    "/evaluation-suites/{suite_id}",
    response_model=EvaluationSuiteResponse,
    operation_id="getSkillEvaluationSuite",
)
def get_evaluation_suite(suite_id: UUID, uow: Uow) -> EvaluationSuiteResponse:
    with uow() as tx:
        suite = tx.skill_evaluation_suites.get(SkillEvaluationSuiteId.parse(str(suite_id)))
        if suite is None:
            raise SkillEvaluationSuiteNotFound(suite_id)
        return EvaluationSuiteResponse(
            id=str(suite.id),
            skill_id=str(suite.skill_id),
            name=suite.name,
            description=suite.description,
            status=suite.status.value,
            created_at=suite.created_at,
            updated_at=suite.updated_at,
            cases=[
                EvaluationCaseResponse(
                    id=str(case.id),
                    suite_id=str(case.suite_id),
                    position=case.position,
                    input=case.input,
                    expected_properties=case.expected_properties,
                    grading_kind=case.grading_kind,
                    created_at=case.created_at,
                    updated_at=case.updated_at,
                )
                for case in tx.skill_evaluation_cases.list_for_suite(suite.id)
            ],
        )


@router.post(
    "/evaluation-suites/{suite_id}/runs",
    response_model=EvaluationRunResponse,
    operation_id="runSkillEvaluation",
)
def run_evaluation(
    suite_id: UUID, body: RunEvaluationBody, uow: Uow, clock: ClockDep
) -> EvaluationRunResponse:
    run = RunSkillEvaluation(uow, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=SkillEvaluationSuiteId.parse(str(suite_id)),
        revision_id=SkillRevisionId.parse(body.revision_id) if body.revision_id else None,
        proposal_id=SkillImprovementProposalId.parse(body.proposal_id)
        if body.proposal_id
        else None,
        outputs=body.outputs,
    )
    return _evaluation_run_response(uow, run)


@router.get(
    "/evaluation-runs/{run_id}",
    response_model=EvaluationRunResponse,
    operation_id="getSkillEvaluationRun",
)
def get_evaluation_run(run_id: UUID, uow: Uow) -> EvaluationRunResponse:
    with uow() as tx:
        run = tx.skill_evaluation_runs.get(SkillEvaluationRunId.parse(str(run_id)))
        if run is None:
            raise SkillEvaluationRunNotFound(run_id)
    return _evaluation_run_response(uow, run)

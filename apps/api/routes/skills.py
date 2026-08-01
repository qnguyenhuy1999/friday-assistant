from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.schemas.skills import (
    CandidateEvaluationResponse,
    CreateEvaluationSuiteBody,
    CreateImprovementProposalBody,
    CreateSkillBody,
    CreateSkillEvidenceSnapshotBody,
    CreateSkillRevisionBody,
    EvaluateImprovementProposalBody,
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
    SkillUsageRecordResponse,
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
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
)
from friday.application.skill_improvement_policy import (
    RunSkillImprovementPolicyNow,
    SaveSkillImprovementPolicy,
)
from friday.application.skill_promotion import (
    ApproveSkillPromotion,
    ApproveSkillRollback,
    RejectSkillPromotion,
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
from friday.domain.json_value import ensure_json_value

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
        result=value.result.value,
        recommendation=value.recommendation.value,
        score_delta=value.score_delta,
        regression_count=value.regression_count,
        improvement_count=value.improvement_count,
        inconclusive_count=value.inconclusive_count,
        report_sha256=value.report_sha256,
    )


def _promotion(x: SkillPromotionRequest) -> SkillPromotionResponse:
    return SkillPromotionResponse(
        id=str(x.id),
        proposal_id=str(x.proposal_id),
        skill_id=str(x.skill_id),
        status=x.status.value,
        authorization_fingerprint=x.authorization_fingerprint,
        target_version=x.target_version,
        promoted_revision_id=str(x.promoted_revision_id) if x.promoted_revision_id else None,
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
def list_skills(uow: Uow) -> SkillPageResponse:
    with uow() as tx:
        return SkillPageResponse(items=[_skill(x) for x in tx.skills.list(100)])


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
def list_revisions(skill_id: UUID, uow: Uow) -> list[SkillRevisionResponse]:
    return [_revision(x) for x in GetSkill(uow).list_revisions(SkillId.parse(str(skill_id)))]


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


@router.get("/{skill_id}/usage", response_model=list[SkillUsageRecordResponse])
def list_usage(skill_id: UUID, uow: Uow) -> list[SkillUsageRecordResponse]:
    with uow() as tx:
        return [
            SkillUsageRecordResponse(
                id=str(x.id),
                run_id=str(x.run_id),
                task_id=str(x.task_id),
                skill_id=str(x.skill_id),
                revision_id=str(x.revision_id),
                outcome=x.outcome.value,
                failure_code=x.failure_code,
                tool_call_count=x.tool_call_count,
                approval_count=x.approval_count,
                duration_ms=x.duration_ms,
                completed_at=x.completed_at,
            )
            for x in tx.skill_usage_records.list_for_skill(SkillId.parse(str(skill_id)), 100)
        ]


@router.post("/{skill_id}/evidence-snapshots", response_model=SkillEvidenceSnapshotResponse)
def create_evidence_snapshot(
    skill_id: UUID, body: CreateSkillEvidenceSnapshotBody, uow: Uow, clock: ClockDep
) -> SkillEvidenceSnapshotResponse:
    snapshot = CreateSkillEvidenceSnapshot(uow, clock).execute(
        skill_id=SkillId.parse(str(skill_id)),
        base_revision_id=SkillRevisionId.parse(body.base_revision_id),
        evidence=ensure_json_value(body.evidence),
    )
    return SkillEvidenceSnapshotResponse(
        id=str(snapshot.id),
        skill_id=str(snapshot.skill_id),
        base_revision_id=str(snapshot.base_revision_id),
        content_sha256=snapshot.content_sha256,
        created_at=snapshot.created_at,
    )


@router.post("/{skill_id}/improvement-proposals", response_model=ImprovementProposalResponse)
def create_improvement_proposal(
    skill_id: UUID, body: CreateImprovementProposalBody, uow: Uow, clock: ClockDep
) -> ImprovementProposalResponse:
    return _proposal(
        CreateSkillImprovementProposal(uow, clock).execute(
            skill_id=SkillId.parse(str(skill_id)),
            base_revision_id=SkillRevisionId.parse(body.base_revision_id),
            trigger_kind=body.trigger_kind,
            evidence_snapshot_id=SkillEvidenceSnapshotId.parse(body.evidence_snapshot_id),
            evidence_snapshot_hash=body.evidence_snapshot_hash,
            evidence_ids=set(body.evidence_ids),
            generator_version=body.generator_version,
            raw_candidate=body.candidate_json,
        )
    )


@router.get("/{skill_id}/improvement-proposals", response_model=list[ImprovementProposalResponse])
def list_improvement_proposals(skill_id: UUID, uow: Uow) -> list[ImprovementProposalResponse]:
    with uow() as tx:
        proposals = tx.skill_improvement_proposals.list_for_skill(SkillId.parse(str(skill_id)))
        return [_proposal(x) for x in proposals]


@router.get("/improvement-proposals/{proposal_id}", response_model=ImprovementProposalResponse)
def get_improvement_proposal(proposal_id: UUID, uow: Uow) -> ImprovementProposalResponse:
    with uow() as tx:
        proposal = tx.skill_improvement_proposals.get(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
        if proposal is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="skill improvement proposal not found")
        return _proposal(proposal)


@router.post(
    "/improvement-proposals/{proposal_id}/cancel", response_model=ImprovementProposalResponse
)
def cancel_improvement_proposal(proposal_id: UUID, uow: Uow) -> ImprovementProposalResponse:
    return _proposal(
        CancelSkillImprovementProposal(uow).execute(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
    )


@router.post(
    "/improvement-proposals/{proposal_id}/evaluate", response_model=CandidateEvaluationResponse
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
    "/improvement-proposals/{proposal_id}/evaluation", response_model=CandidateEvaluationResponse
)
def get_improvement_evaluation(proposal_id: UUID, uow: Uow) -> CandidateEvaluationResponse:
    with uow() as tx:
        comparison = tx.skill_candidate_evaluations.get_for_proposal(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
        if comparison is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="candidate evaluation not found")
        return _candidate_evaluation(comparison)


@router.post(
    "/improvement-proposals/{proposal_id}/request-promotion",
    response_model=SkillPromotionResponse,
)
def request_promotion(proposal_id: UUID, uow: Uow, clock: ClockDep) -> SkillPromotionResponse:
    return _promotion(
        RequestSkillPromotion(uow, clock).execute(
            SkillImprovementProposalId.parse(str(proposal_id))
        )
    )


@router.post("/promotions/{promotion_id}/approve", response_model=SkillPromotionResponse)
def approve_promotion(
    promotion_id: UUID, body: ResolveSkillRequestBody, uow: Uow, clock: ClockDep
) -> SkillPromotionResponse:
    return _promotion(
        ApproveSkillPromotion(uow, clock).execute(
            SkillPromotionRequestId.parse(str(promotion_id)), body.resolver
        )
    )


@router.post("/promotions/{promotion_id}/reject", response_model=SkillPromotionResponse)
def reject_promotion(
    promotion_id: UUID, body: ResolveSkillRequestBody, uow: Uow, clock: ClockDep
) -> SkillPromotionResponse:
    return _promotion(
        RejectSkillPromotion(uow, clock).execute(
            SkillPromotionRequestId.parse(str(promotion_id)), body.resolver
        )
    )


@router.post("/{skill_id}/request-rollback")
def request_rollback(
    skill_id: UUID, body: RequestRollbackBody, uow: Uow, clock: ClockDep
) -> dict[str, str]:
    request = RequestSkillRollback(uow, clock).execute(
        skill_id=SkillId.parse(str(skill_id)),
        target_revision_id=SkillRevisionId.parse(body.target_revision_id),
        reason=body.reason,
    )
    return {"id": str(request.id), "status": request.status.value}


@router.post("/rollbacks/{rollback_id}/approve")
def approve_rollback(
    rollback_id: UUID, body: ResolveSkillRequestBody, uow: Uow, clock: ClockDep
) -> dict[str, str]:
    request = ApproveSkillRollback(uow, clock).execute(
        SkillRollbackRequestId.parse(str(rollback_id)), body.resolver
    )
    return {"id": str(request.id), "status": request.status.value}


@router.get("/{skill_id}/improvement-policy", response_model=SkillImprovementPolicyResponse)
def get_improvement_policy(skill_id: UUID, uow: Uow) -> SkillImprovementPolicyResponse:
    with uow() as tx:
        policy = tx.skill_improvement_policies.get(SkillId.parse(str(skill_id)))
        if policy is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="skill improvement policy not found")
        return _policy(policy)


@router.put("/{skill_id}/improvement-policy", response_model=SkillImprovementPolicyResponse)
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


@router.post("/{skill_id}/improvement-policy/run-now")
def run_improvement_policy_now(skill_id: UUID, uow: Uow, clock: ClockDep) -> dict[str, bool]:
    return {"due": RunSkillImprovementPolicyNow(uow, clock).execute(SkillId.parse(str(skill_id)))}


@router.post("/{skill_id}/evaluation-suites", response_model=EvaluationSuiteResponse)
def create_evaluation_suite(
    skill_id: UUID, body: CreateEvaluationSuiteBody, uow: Uow, clock: ClockDep
) -> EvaluationSuiteResponse:
    suite = CreateSkillEvaluationSuite(uow, clock).execute(
        skill_id=SkillId.parse(str(skill_id)),
        name=body.name,
        description=body.description,
        cases=[(x.input, x.expected_properties, x.grading_kind) for x in body.cases],
    )
    return EvaluationSuiteResponse(
        id=str(suite.id),
        skill_id=str(suite.skill_id),
        name=suite.name,
        description=suite.description,
        status=suite.status.value,
        created_at=suite.created_at,
    )


@router.get("/{skill_id}/evaluation-suites", response_model=list[EvaluationSuiteResponse])
def list_evaluation_suites(skill_id: UUID, uow: Uow) -> list[EvaluationSuiteResponse]:
    with uow() as tx:
        return [
            EvaluationSuiteResponse(
                id=str(x.id),
                skill_id=str(x.skill_id),
                name=x.name,
                description=x.description,
                status=x.status.value,
                created_at=x.created_at,
            )
            for x in tx.skill_evaluation_suites.list_for_skill(SkillId.parse(str(skill_id)))
        ]


@router.post("/evaluation-suites/{suite_id}/runs", response_model=EvaluationRunResponse)
def run_evaluation(
    suite_id: UUID, body: RunEvaluationBody, uow: Uow, clock: ClockDep
) -> EvaluationRunResponse:
    run = RunSkillEvaluation(uow, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=SkillEvaluationSuiteId.parse(str(suite_id)),
        revision_id=SkillRevisionId.parse(body.revision_id),
        outputs=body.outputs,
    )
    return EvaluationRunResponse(
        id=str(run.id),
        suite_id=str(run.suite_id),
        skill_id=str(run.skill_id),
        revision_id=str(run.revision_id),
        status=run.status.value,
        aggregate_result=run.aggregate_result,
        runtime_fingerprint=run.runtime_fingerprint,
    )


@router.get("/evaluation-runs/{run_id}", response_model=EvaluationRunResponse)
def get_evaluation_run(run_id: UUID, uow: Uow) -> EvaluationRunResponse:
    with uow() as tx:
        run = tx.skill_evaluation_runs.get(SkillEvaluationRunId.parse(str(run_id)))
        if run is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="evaluation run not found")
    return EvaluationRunResponse(
        id=str(run.id),
        suite_id=str(run.suite_id),
        skill_id=str(run.skill_id),
        revision_id=str(run.revision_id),
        status=run.status.value,
        aggregate_result=run.aggregate_result,
        runtime_fingerprint=run.runtime_fingerprint,
    )

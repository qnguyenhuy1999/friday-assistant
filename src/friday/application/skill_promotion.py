"""Exact approval-gated Skill promotion and rollback transactions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime

from friday.application.approval_workflow import transition_approval_in_transaction
from friday.application.errors import EntityConflict, SkillNotFound
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.domain import (
    ApprovalCategory,
    ApprovalRequest,
    ApprovalRequestId,
    ApprovalStatus,
    CandidateRecommendation,
    PromotionRequestStatus,
    RollbackRequestStatus,
    SkillId,
    SkillImprovementProposalId,
    SkillPromotionRequest,
    SkillPromotionRequestId,
    SkillRevision,
    SkillRevisionId,
    SkillRevisionSourceKind,
    SkillRollbackRequest,
    SkillRollbackRequestId,
    SkillStatus,
)
from friday.domain.approval import ApprovalSubjectKind
from friday.domain.skill_improvement import SkillImprovementProposal, SkillProposalStatus


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _promotion_fingerprint(request: SkillPromotionRequest) -> str:
    return _fingerprint(
        {
            "version": 1,
            "promotion_request_id": str(request.id),
            "approval_request_id": str(request.approval_request_id)
            if request.approval_request_id
            else None,
            "proposal_id": str(request.proposal_id),
            "skill_id": str(request.skill_id),
            "base_revision_id": str(request.base_revision_id),
            "current_active_revision_id": str(request.expected_active_revision_id),
            "candidate_sha256": request.candidate_sha256,
            "candidate_evaluation_id": str(request.candidate_evaluation_id),
            "comparison_report_sha256": request.comparison_report_sha256,
            "target_revision_id": str(request.target_revision_id),
            "target_version": request.target_version,
        }
    )


def _rollback_fingerprint(request: SkillRollbackRequest) -> str:
    return _fingerprint(
        {
            "version": 1,
            "rollback_request_id": str(request.id),
            "approval_request_id": str(request.approval_request_id)
            if request.approval_request_id
            else None,
            "skill_id": str(request.skill_id),
            "current_revision_id": str(request.expected_current_revision_id),
            "target_revision_id": str(request.target_revision_id),
            "reason": request.reason,
        }
    )


_CLOSED_PROPOSAL_STATUSES = frozenset(
    {
        SkillProposalStatus.REJECTED,
        SkillProposalStatus.SUPERSEDED,
        SkillProposalStatus.CANCELLED,
        SkillProposalStatus.EXPIRED,
        SkillProposalStatus.PROMOTED,
    }
)


def _load_promotion_proposal(
    uow: UnitOfWork, request: SkillPromotionRequest
) -> SkillImprovementProposal:
    proposal = uow.skill_improvement_proposals.get(request.proposal_id)
    if proposal is None:
        raise EntityConflict("promotion proposal was not found")
    if proposal.id != request.proposal_id or proposal.skill_id != request.skill_id:
        raise EntityConflict("promotion proposal does not belong to request")
    if proposal.status in _CLOSED_PROPOSAL_STATUSES:
        raise EntityConflict("promotion proposal is already closed")
    return proposal


def _load_promotion_approval(
    uow: UnitOfWork, request: SkillPromotionRequest
) -> ApprovalRequest | None:
    if request.approval_request_id is None:
        return None
    approval = uow.approvals.get(request.approval_request_id)
    if approval is None:
        raise EntityConflict("promotion approval was not found")
    if (
        approval.subject_kind is not ApprovalSubjectKind.SKILL_PROMOTION
        or approval.subject_id != str(request.id)
        or approval.authorization_fingerprint != request.authorization_fingerprint
    ):
        raise EntityConflict("promotion approval does not belong to request")
    return approval


def _load_rollback_approval(
    uow: UnitOfWork, request: SkillRollbackRequest
) -> ApprovalRequest | None:
    if request.approval_request_id is None:
        return None
    approval = uow.approvals.get(request.approval_request_id)
    if approval is None:
        raise EntityConflict("rollback approval was not found")
    if (
        approval.subject_kind is not ApprovalSubjectKind.SKILL_ROLLBACK
        or approval.subject_id != str(request.id)
        or approval.authorization_fingerprint != request.authorization_fingerprint
    ):
        raise EntityConflict("rollback approval does not belong to request")
    return approval


def _stage_terminal_promotion(
    uow: UnitOfWork,
    request: SkillPromotionRequest,
    proposal: SkillImprovementProposal,
    *,
    promotion_status: PromotionRequestStatus,
    proposal_status: SkillProposalStatus,
    now: datetime,
    resolver: str | None = None,
    approval: ApprovalRequest | None = None,
    approval_status: ApprovalStatus | None = None,
    resolution_note: str | None = None,
) -> SkillPromotionRequest:
    if approval_status is not None:
        if approval is None:
            raise EntityConflict("promotion request has no canonical approval")
        transition_approval_in_transaction(
            uow,
            approval,
            approval_status,
            now,
            resolver=resolver,
            resolution_note=resolution_note,
        )
    terminal = replace(
        request,
        status=promotion_status,
        resolved_at=now,
        resolver=resolver,
    )
    uow.skill_promotion_requests.save(terminal)
    uow.skill_improvement_proposals.save(replace(proposal, status=proposal_status))
    return terminal


class RequestSkillPromotion:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, proposal_id: SkillImprovementProposalId) -> SkillPromotionRequest:
        with self._uow_factory() as uow:
            proposal = uow.skill_improvement_proposals.get(proposal_id)
            comparison = uow.skill_candidate_evaluations.get_for_proposal(proposal_id)
            if (
                proposal is None
                or comparison is None
                or proposal.status is not SkillProposalStatus.READY_FOR_REVIEW
            ):
                raise EntityConflict("proposal is not ready for promotion review")
            if comparison.recommendation is not CandidateRecommendation.ELIGIBLE:
                raise EntityConflict("comparison is not eligible for ordinary promotion approval")
            skill = uow.skills.get(proposal.skill_id)
            if skill is None:
                raise SkillNotFound(proposal.skill_id)
            if skill.status is not SkillStatus.ACTIVE:
                raise EntityConflict("only an active skill can be promoted")
            if skill.active_revision_id != proposal.base_revision_id:
                raise EntityConflict("proposal base is no longer active")
            target_revision_id = SkillRevisionId.new()
            approval_id = ApprovalRequestId.new()
            request = SkillPromotionRequest(
                id=SkillPromotionRequestId.new(),
                proposal_id=proposal.id,
                skill_id=skill.id,
                base_revision_id=proposal.base_revision_id,
                expected_active_revision_id=skill.active_revision_id,
                candidate_sha256=proposal.proposed_content_sha256,
                candidate_evaluation_id=comparison.id,
                comparison_report_sha256=comparison.report_sha256,
                target_revision_id=target_revision_id,
                target_version=uow.skill_revisions.next_version(skill.id),
                authorization_fingerprint="0" * 64,
                status=PromotionRequestStatus.PENDING,
                created_at=self._clock.now(),
                approval_request_id=approval_id,
            )
            request = replace(request, authorization_fingerprint=_promotion_fingerprint(request))
            uow.skill_promotion_requests.add(request)
            uow.approvals.add(
                ApprovalRequest.new(
                    id=approval_id,
                    run_id=None,
                    category=ApprovalCategory.OTHER,
                    summary="Approve Skill promotion",
                    reason="A reviewed Skill candidate is ready to become active.",
                    requested_action="skill.promote",
                    requested_input={
                        "promotion_request_id": str(request.id),
                        "approval_request_id": str(approval_id),
                        "proposal_id": str(request.proposal_id),
                        "skill_id": str(request.skill_id),
                        "base_revision_id": str(request.base_revision_id),
                        "current_active_revision_id": str(request.expected_active_revision_id),
                        "target_revision_id": str(request.target_revision_id),
                        "candidate_instructions": proposal.proposed_instructions,
                        "candidate_sha256": request.candidate_sha256,
                        "candidate_evaluation_id": str(request.candidate_evaluation_id),
                        "comparison_report_sha256": request.comparison_report_sha256,
                        "target_version": request.target_version,
                        "evidence_snapshot_id": str(proposal.evidence_snapshot_id),
                        "evidence_snapshot_hash": proposal.evidence_snapshot_hash,
                        "comparison_report": comparison.comparison_report,
                        "recommendation": comparison.recommendation.value,
                        "authorization_fingerprint": request.authorization_fingerprint,
                    },
                    requested_at=request.created_at,
                    authorization_fingerprint=request.authorization_fingerprint,
                    subject_kind=ApprovalSubjectKind.SKILL_PROMOTION,
                    subject_id=str(request.id),
                )
            )
            uow.commit()
            return request


class ExecuteSkillPromotion:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self, request_id: SkillPromotionRequestId, resolver: str | None = None
    ) -> SkillPromotionRequest:
        with self._uow_factory() as uow:
            request = uow.skill_promotion_requests.get(request_id)
            if request is None:
                raise EntityConflict("promotion request was not found")
            if request.status is not PromotionRequestStatus.PENDING:
                raise EntityConflict("promotion request is not pending")
            proposal = _load_promotion_proposal(uow, request)
            approval = _load_promotion_approval(uow, request)
            if approval is None:
                raise EntityConflict("promotion request has no canonical approval")
            if approval.status is not ApprovalStatus.APPROVED or approval.is_consumed:
                raise EntityConflict("promotion requires one unconsumed approved request")
            comparison = uow.skill_candidate_evaluations.get_for_proposal(request.proposal_id)
            skill = uow.skills.get(request.skill_id)
            stale = (
                proposal is None
                or comparison is None
                or skill is None
                or proposal.status is not SkillProposalStatus.READY_FOR_REVIEW
                or proposal.base_revision_id != request.base_revision_id
                or proposal.proposed_content_sha256 != request.candidate_sha256
                or comparison.id != request.candidate_evaluation_id
                or comparison.report_sha256 != request.comparison_report_sha256
                or comparison.recommendation is not CandidateRecommendation.ELIGIBLE
                or skill.status is not SkillStatus.ACTIVE
                or skill.active_revision_id != request.expected_active_revision_id
                or uow.skill_revisions.next_version(skill.id) != request.target_version
                or _promotion_fingerprint(request) != request.authorization_fingerprint
                or approval.authorization_fingerprint != request.authorization_fingerprint
                or approval.subject_kind is not ApprovalSubjectKind.SKILL_PROMOTION
                or approval.subject_id != str(request.id)
            )
            now = self._clock.now()
            if stale:
                _stage_terminal_promotion(
                    uow,
                    request,
                    proposal,
                    promotion_status=PromotionRequestStatus.STALE,
                    proposal_status=SkillProposalStatus.SUPERSEDED,
                    now=now,
                    resolver=resolver or approval.resolver,
                    approval=approval,
                )
                uow.commit()
                raise EntityConflict("promotion request is stale")
            assert skill is not None
            revision = SkillRevision.new(
                id=request.target_revision_id,
                skill_id=skill.id,
                version=request.target_version,
                instructions=proposal.proposed_instructions,
                source_kind=SkillRevisionSourceKind.GENERATED,
                created_at=now,
                promotion_request_id=str(request.id),
            )
            uow.skill_revisions.add(revision)
            completed = replace(request, promoted_revision_id=revision.id)
            completed = _stage_terminal_promotion(
                uow,
                completed,
                proposal,
                promotion_status=PromotionRequestStatus.PROMOTED,
                proposal_status=SkillProposalStatus.PROMOTED,
                now=now,
                resolver=resolver or approval.resolver,
                approval=approval,
            )
            # Stage the promotion row before the active-pointer update.  The
            # final database trigger permits a generated active revision only
            # when this exact promotion row already names it as promoted.
            skill.activate(revision, now)
            uow.skills.save(skill)
            consume_if_unconsumed = getattr(uow.approvals, "consume_if_unconsumed", None)
            if callable(consume_if_unconsumed):
                if not consume_if_unconsumed(approval.id, now):
                    raise EntityConflict("promotion approval was already consumed")
            else:
                approval.consume(now)
                uow.approvals.save(approval)
            uow.commit()
            return completed


class RejectSkillPromotion:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, request_id: SkillPromotionRequestId, resolver: str) -> SkillPromotionRequest:
        with self._uow_factory() as uow:
            request = uow.skill_promotion_requests.get(request_id)
            if request is None or request.status is not PromotionRequestStatus.PENDING:
                raise EntityConflict("promotion request is not pending")
            proposal = _load_promotion_proposal(uow, request)
            approval = _load_promotion_approval(uow, request)
            rejected = _stage_terminal_promotion(
                uow,
                request,
                proposal,
                promotion_status=PromotionRequestStatus.REJECTED,
                proposal_status=SkillProposalStatus.REJECTED,
                now=self._clock.now(),
                resolver=resolver,
                approval=approval,
                approval_status=ApprovalStatus.REJECTED if approval is not None else None,
            )
            uow.commit()
            return rejected


class CancelSkillPromotion:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, request_id: SkillPromotionRequestId) -> SkillPromotionRequest:
        with self._uow_factory() as uow:
            request = uow.skill_promotion_requests.get(request_id)
            if request is None or request.status is not PromotionRequestStatus.PENDING:
                raise EntityConflict("promotion request is not pending")
            proposal = _load_promotion_proposal(uow, request)
            approval = _load_promotion_approval(uow, request)
            cancelled = _stage_terminal_promotion(
                uow,
                request,
                proposal,
                promotion_status=PromotionRequestStatus.CANCELLED,
                proposal_status=SkillProposalStatus.CANCELLED,
                now=self._clock.now(),
                approval=approval,
                approval_status=ApprovalStatus.CANCELLED if approval is not None else None,
            )
            uow.commit()
            return cancelled


class RequestSkillRollback:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self, *, skill_id: SkillId, target_revision_id: SkillRevisionId, reason: str
    ) -> SkillRollbackRequest:
        with self._uow_factory() as uow:
            skill = uow.skills.get(skill_id)
            target = uow.skill_revisions.get(target_revision_id)
            if skill is None:
                raise SkillNotFound(skill_id)
            if (
                skill.status is not SkillStatus.ACTIVE
                or skill.active_revision_id is None
                or target is None
                or target.skill_id != skill_id
            ):
                raise EntityConflict("rollback target or current revision is invalid")
            if target_revision_id == skill.active_revision_id:
                raise EntityConflict("rollback target is already active")
            approval_id = ApprovalRequestId.new()
            request = SkillRollbackRequest(
                id=SkillRollbackRequestId.new(),
                skill_id=skill_id,
                expected_current_revision_id=skill.active_revision_id,
                target_revision_id=target_revision_id,
                reason=reason,
                authorization_fingerprint="0" * 64,
                status=RollbackRequestStatus.PENDING,
                created_at=self._clock.now(),
                approval_request_id=approval_id,
            )
            request = replace(request, authorization_fingerprint=_rollback_fingerprint(request))
            uow.skill_rollback_requests.add(request)
            uow.approvals.add(
                ApprovalRequest.new(
                    id=approval_id,
                    run_id=None,
                    category=ApprovalCategory.OTHER,
                    summary="Approve Skill rollback",
                    reason=request.reason,
                    requested_action="skill.rollback",
                    requested_input={
                        "rollback_request_id": str(request.id),
                        "skill_id": str(request.skill_id),
                        "target_revision_id": str(request.target_revision_id),
                        "authorization_fingerprint": request.authorization_fingerprint,
                    },
                    requested_at=request.created_at,
                    authorization_fingerprint=request.authorization_fingerprint,
                    subject_kind=ApprovalSubjectKind.SKILL_ROLLBACK,
                    subject_id=str(request.id),
                )
            )
            uow.commit()
            return request


class ExecuteSkillRollback:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self, request_id: SkillRollbackRequestId, resolver: str | None = None
    ) -> SkillRollbackRequest:
        with self._uow_factory() as uow:
            request = uow.skill_rollback_requests.get(request_id)
            if request is None:
                raise EntityConflict("rollback request was not found")
            if request.status is RollbackRequestStatus.COMPLETED:
                return request
            if request.status is not RollbackRequestStatus.PENDING:
                raise EntityConflict("rollback request is not pending")
            if request.approval_request_id is None:
                raise EntityConflict("rollback request has no canonical approval")
            approval = uow.approvals.get(request.approval_request_id)
            if approval is None:
                raise EntityConflict("rollback approval was not found")
            if approval.status is not ApprovalStatus.APPROVED or approval.is_consumed:
                raise EntityConflict("rollback requires one unconsumed approved request")
            skill = uow.skills.get(request.skill_id)
            target = uow.skill_revisions.get(request.target_revision_id)
            now = self._clock.now()
            if (
                skill is None
                or skill.status is not SkillStatus.ACTIVE
                or target is None
                or target.skill_id != request.skill_id
                or target.id == skill.active_revision_id
                or skill.active_revision_id != request.expected_current_revision_id
                or _rollback_fingerprint(request) != request.authorization_fingerprint
                or approval.authorization_fingerprint != request.authorization_fingerprint
                or approval.subject_kind is not ApprovalSubjectKind.SKILL_ROLLBACK
                or approval.subject_id != str(request.id)
            ):
                uow.skill_rollback_requests.save(
                    replace(
                        request,
                        status=RollbackRequestStatus.STALE,
                        resolved_at=now,
                        resolver=resolver or approval.resolver,
                    )
                )
                uow.commit()
                raise EntityConflict("rollback request is stale")
            skill.activate(target, now)
            uow.skills.save(skill)
            completed = replace(
                request,
                status=RollbackRequestStatus.COMPLETED,
                resolved_at=now,
                resolver=resolver or approval.resolver,
            )
            uow.skill_rollback_requests.save(completed)
            consume_if_unconsumed = getattr(uow.approvals, "consume_if_unconsumed", None)
            if callable(consume_if_unconsumed):
                if not consume_if_unconsumed(approval.id, now):
                    raise EntityConflict("rollback approval was already consumed")
            else:
                approval.consume(now)
                uow.approvals.save(approval)
            uow.commit()
            return completed


class RejectSkillRollback:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, request_id: SkillRollbackRequestId, resolver: str) -> SkillRollbackRequest:
        with self._uow_factory() as uow:
            request = uow.skill_rollback_requests.get(request_id)
            if request is None or request.status is not RollbackRequestStatus.PENDING:
                raise EntityConflict("rollback request is not pending")
            approval = _load_rollback_approval(uow, request)
            if approval is not None:
                transition_approval_in_transaction(
                    uow,
                    approval,
                    ApprovalStatus.REJECTED,
                    self._clock.now(),
                    resolver=resolver,
                )
            rejected = replace(
                request,
                status=RollbackRequestStatus.REJECTED,
                resolved_at=self._clock.now(),
                resolver=resolver,
            )
            uow.skill_rollback_requests.save(rejected)
            uow.commit()
            return rejected


class CancelSkillRollback:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, request_id: SkillRollbackRequestId) -> SkillRollbackRequest:
        with self._uow_factory() as uow:
            request = uow.skill_rollback_requests.get(request_id)
            if request is None or request.status is not RollbackRequestStatus.PENDING:
                raise EntityConflict("rollback request is not pending")
            approval = _load_rollback_approval(uow, request)
            if approval is not None:
                transition_approval_in_transaction(
                    uow,
                    approval,
                    ApprovalStatus.CANCELLED,
                    self._clock.now(),
                )
            cancelled = replace(
                request,
                status=RollbackRequestStatus.CANCELLED,
                resolved_at=self._clock.now(),
            )
            uow.skill_rollback_requests.save(cancelled)
            uow.commit()
            return cancelled

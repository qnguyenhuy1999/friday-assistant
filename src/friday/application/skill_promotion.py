"""Exact approval-gated Skill promotion and rollback transactions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from friday.application.errors import EntityConflict, SkillNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
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
from friday.domain.skill_improvement import SkillProposalStatus


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _promotion_fingerprint(request: SkillPromotionRequest) -> str:
    return _fingerprint(
        {
            "version": 1,
            "promotion_request_id": str(request.id),
            "proposal_id": str(request.proposal_id),
            "skill_id": str(request.skill_id),
            "base_revision_id": str(request.base_revision_id),
            "current_active_revision_id": str(request.expected_active_revision_id),
            "candidate_sha256": request.candidate_sha256,
            "candidate_evaluation_id": str(request.candidate_evaluation_id),
            "comparison_report_sha256": request.comparison_report_sha256,
            "target_version": request.target_version,
        }
    )


def _rollback_fingerprint(request: SkillRollbackRequest) -> str:
    return _fingerprint(
        {
            "version": 1,
            "rollback_request_id": str(request.id),
            "skill_id": str(request.skill_id),
            "current_revision_id": str(request.expected_current_revision_id),
            "target_revision_id": str(request.target_revision_id),
            "reason": request.reason,
        }
    )


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
            skill = uow.skills.get(proposal.skill_id)
            if skill is None:
                raise SkillNotFound(proposal.skill_id)
            if skill.status is not SkillStatus.ACTIVE:
                raise EntityConflict("only an active skill can be promoted")
            if skill.active_revision_id != proposal.base_revision_id:
                raise EntityConflict("proposal base is no longer active")
            request = SkillPromotionRequest(
                id=SkillPromotionRequestId.new(),
                proposal_id=proposal.id,
                skill_id=skill.id,
                base_revision_id=proposal.base_revision_id,
                expected_active_revision_id=skill.active_revision_id,
                candidate_sha256=proposal.proposed_content_sha256,
                candidate_evaluation_id=comparison.id,
                comparison_report_sha256=comparison.report_sha256,
                target_version=uow.skill_revisions.next_version(skill.id),
                authorization_fingerprint="0" * 64,
                status=PromotionRequestStatus.PENDING,
                created_at=self._clock.now(),
            )
            request = replace(request, authorization_fingerprint=_promotion_fingerprint(request))
            uow.skill_promotion_requests.add(request)
            uow.commit()
            return request


class ApproveSkillPromotion:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, request_id: SkillPromotionRequestId, resolver: str) -> SkillPromotionRequest:
        with self._uow_factory() as uow:
            request = uow.skill_promotion_requests.get(request_id)
            if request is None or request.status is not PromotionRequestStatus.PENDING:
                raise EntityConflict("promotion request is not pending")
            proposal = uow.skill_improvement_proposals.get(request.proposal_id)
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
                or skill.status is not SkillStatus.ACTIVE
                or skill.active_revision_id != request.expected_active_revision_id
                or uow.skill_revisions.next_version(skill.id) != request.target_version
                or _promotion_fingerprint(request) != request.authorization_fingerprint
            )
            now = self._clock.now()
            if stale:
                uow.skill_promotion_requests.save(
                    replace(
                        request,
                        status=PromotionRequestStatus.STALE,
                        resolved_at=now,
                        resolver=resolver,
                    )
                )
                uow.commit()
                raise EntityConflict("promotion request is stale")
            assert proposal is not None
            assert skill is not None
            revision = SkillRevision.new(
                id=SkillRevisionId.new(),
                skill_id=skill.id,
                version=request.target_version,
                instructions=proposal.proposed_instructions,
                source_kind=SkillRevisionSourceKind.GENERATED,
                created_at=now,
            )
            uow.skill_revisions.add(revision)
            skill.activate(revision, now)
            uow.skills.save(skill)
            uow.skill_improvement_proposals.save(
                replace(proposal, status=SkillProposalStatus.PROMOTED)
            )
            completed = replace(
                request,
                status=PromotionRequestStatus.PROMOTED,
                resolved_at=now,
                resolver=resolver,
                promoted_revision_id=revision.id,
            )
            uow.skill_promotion_requests.save(completed)
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
            rejected = replace(
                request,
                status=PromotionRequestStatus.REJECTED,
                resolved_at=self._clock.now(),
                resolver=resolver,
            )
            uow.skill_promotion_requests.save(rejected)
            uow.commit()
            return rejected


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
            request = SkillRollbackRequest(
                id=SkillRollbackRequestId.new(),
                skill_id=skill_id,
                expected_current_revision_id=skill.active_revision_id,
                target_revision_id=target_revision_id,
                reason=reason,
                authorization_fingerprint="0" * 64,
                status=RollbackRequestStatus.PENDING,
                created_at=self._clock.now(),
            )
            request = replace(request, authorization_fingerprint=_rollback_fingerprint(request))
            uow.skill_rollback_requests.add(request)
            uow.commit()
            return request


class ApproveSkillRollback:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, request_id: SkillRollbackRequestId, resolver: str) -> SkillRollbackRequest:
        with self._uow_factory() as uow:
            request = uow.skill_rollback_requests.get(request_id)
            if request is None or request.status is not RollbackRequestStatus.PENDING:
                raise EntityConflict("rollback request is not pending")
            skill = uow.skills.get(request.skill_id)
            target = uow.skill_revisions.get(request.target_revision_id)
            now = self._clock.now()
            if (
                skill is None
                or skill.status is not SkillStatus.ACTIVE
                or target is None
                or target.skill_id != request.skill_id
                or skill.active_revision_id != request.expected_current_revision_id
                or _rollback_fingerprint(request) != request.authorization_fingerprint
            ):
                uow.skill_rollback_requests.save(
                    replace(
                        request,
                        status=RollbackRequestStatus.STALE,
                        resolved_at=now,
                        resolver=resolver,
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
                resolver=resolver,
            )
            uow.skill_rollback_requests.save(completed)
            uow.commit()
            return completed

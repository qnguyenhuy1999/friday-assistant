"""Validate bounded brain-only candidate output; this module never executes it."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Protocol

from friday.application.errors import EntityConflict, SkillNotFound, SkillRevisionNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    SkillEvidenceSnapshot,
    SkillEvidenceSnapshotId,
    SkillId,
    SkillImprovementProposal,
    SkillImprovementProposalId,
    SkillProposalStatus,
    SkillRevisionId,
)
from friday.domain.errors import DomainValidationError
from friday.domain.json_value import JsonValue


@dataclass(frozen=True, slots=True)
class CandidateOutput:
    proposed_instructions: str
    rationale: str
    addressed_evidence_ids: tuple[str, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class CandidateGenerationRequest:
    """Bounded, policy-selected input for a brain-only candidate generator."""

    base_instructions: str
    evidence_snapshot_hash: str
    evidence_ids: tuple[str, ...]
    feedback_summaries: tuple[str, ...]
    evaluator_summaries: tuple[str, ...]
    max_response_chars: int = 40_000

    def __post_init__(self) -> None:
        if not self.base_instructions or len(self.base_instructions) > 32_000:
            raise DomainValidationError("candidate base instructions must be bounded")
        if len(self.evidence_snapshot_hash) != 64 or self.max_response_chars < 1:
            raise DomainValidationError("candidate generation request is invalid")
        if len(self.evidence_ids) > 200 or any(len(item) > 128 for item in self.evidence_ids):
            raise DomainValidationError("candidate evidence package is bounded")


class BrainOnlyCandidateGenerator(Protocol):
    """Must be backed by an adapter with tools/MCP/session persistence disabled."""

    def generate_candidate(self, request: CandidateGenerationRequest) -> str: ...


class CreateSkillEvidenceSnapshot:
    """Freeze a bounded evidence package before any brain call is made."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        *,
        skill_id: SkillId,
        base_revision_id: SkillRevisionId,
        evidence: JsonValue,
    ) -> SkillEvidenceSnapshot:
        with self._uow_factory() as uow:
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound(skill_id)
            base = uow.skill_revisions.get(base_revision_id)
            if base is None:
                raise SkillRevisionNotFound(base_revision_id)
            if base.skill_id != skill_id:
                raise EntityConflict("evidence snapshot base revision does not belong to skill")
            snapshot = SkillEvidenceSnapshot.new(
                id=SkillEvidenceSnapshotId.new(),
                skill_id=skill_id,
                base_revision_id=base_revision_id,
                evidence=evidence,
                created_at=self._clock.now(),
            )
            uow.skill_evidence_snapshots.add(snapshot)
            uow.commit()
            return snapshot


def parse_candidate_output(raw: str, allowed_evidence_ids: set[str]) -> CandidateOutput:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DomainValidationError("candidate output must be strict JSON") from exc
    required_fields = {
        "version",
        "proposed_instructions",
        "rationale",
        "addressed_evidence_ids",
    }
    if not isinstance(value, dict) or set(value) != required_fields:
        raise DomainValidationError("candidate output fields must match the strict contract")
    if (
        value.get("version") != 1
        or not isinstance(value["proposed_instructions"], str)
        or not isinstance(value["rationale"], str)
    ):
        raise DomainValidationError("candidate output has invalid scalar fields")
    ids = value["addressed_evidence_ids"]
    if not isinstance(ids, list) or not all(
        isinstance(x, str) and x in allowed_evidence_ids for x in ids
    ):
        raise DomainValidationError(
            "candidate output references evidence outside the frozen snapshot"
        )
    instructions = value["proposed_instructions"]
    if not instructions or len(instructions) > 32000 or len(value["rationale"]) > 8000:
        raise DomainValidationError("candidate output exceeds bounded fields")
    try:
        instructions.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise DomainValidationError("candidate instructions must be UTF-8") from exc
    return CandidateOutput(
        proposed_instructions=instructions,
        rationale=value["rationale"],
        addressed_evidence_ids=tuple(ids),
        content_sha256=hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
    )


class CreateSkillImprovementProposal:
    """Persist an inert candidate after a brain-only caller has returned JSON."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        *,
        skill_id: SkillId,
        base_revision_id: SkillRevisionId,
        trigger_kind: str,
        evidence_snapshot_id: SkillEvidenceSnapshotId,
        evidence_snapshot_hash: str,
        evidence_ids: set[str],
        generator_version: str,
        raw_candidate: str,
    ) -> SkillImprovementProposal:
        candidate = parse_candidate_output(raw_candidate, evidence_ids)
        with self._uow_factory() as uow:
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound(skill_id)
            base = uow.skill_revisions.get(base_revision_id)
            if base is None:
                raise SkillRevisionNotFound(base_revision_id)
            if base.skill_id != skill_id:
                raise EntityConflict("proposal base revision does not belong to skill")
            snapshot = uow.skill_evidence_snapshots.get(evidence_snapshot_id)
            if snapshot is None:
                raise EntityConflict("evidence snapshot not found")
            if snapshot.skill_id != skill_id or snapshot.base_revision_id != base_revision_id:
                raise EntityConflict("evidence snapshot does not match proposal base")
            if snapshot.content_sha256 != evidence_snapshot_hash:
                raise EntityConflict("evidence snapshot hash does not match frozen snapshot")
            if candidate.content_sha256 == base.content_sha256:
                raise EntityConflict("candidate must differ from base revision")
            proposal = SkillImprovementProposal(
                id=SkillImprovementProposalId.new(),
                skill_id=skill_id,
                base_revision_id=base_revision_id,
                status=SkillProposalStatus.READY_FOR_EVALUATION,
                trigger_kind=trigger_kind,
                evidence_snapshot_id=evidence_snapshot_id,
                evidence_snapshot_hash=evidence_snapshot_hash,
                proposed_instructions=candidate.proposed_instructions,
                proposed_content_sha256=candidate.content_sha256,
                rationale=candidate.rationale,
                generator_version=generator_version,
                created_at=self._clock.now(),
            )
            uow.skill_improvement_proposals.add(proposal)
            uow.commit()
            return proposal


class GenerateSkillImprovementProposal:
    """Calls the brain-only generator outside a transaction, then persists inert output."""

    def __init__(
        self,
        generator: BrainOnlyCandidateGenerator,
        create_proposal: CreateSkillImprovementProposal,
    ) -> None:
        self._generator, self._create_proposal = generator, create_proposal

    def execute(
        self,
        *,
        skill_id: SkillId,
        base_revision_id: SkillRevisionId,
        trigger_kind: str,
        evidence_snapshot_id: SkillEvidenceSnapshotId,
        evidence_snapshot_hash: str,
        evidence_ids: set[str],
        feedback_summaries: tuple[str, ...],
        evaluator_summaries: tuple[str, ...],
        generator_version: str,
        base_instructions: str,
    ) -> SkillImprovementProposal:
        raw_candidate = self._generator.generate_candidate(
            CandidateGenerationRequest(
                base_instructions=base_instructions,
                evidence_snapshot_hash=evidence_snapshot_hash,
                evidence_ids=tuple(sorted(evidence_ids)),
                feedback_summaries=feedback_summaries,
                evaluator_summaries=evaluator_summaries,
            )
        )
        return self._create_proposal.execute(
            skill_id=skill_id,
            base_revision_id=base_revision_id,
            trigger_kind=trigger_kind,
            evidence_snapshot_id=evidence_snapshot_id,
            evidence_snapshot_hash=evidence_snapshot_hash,
            evidence_ids=evidence_ids,
            generator_version=generator_version,
            raw_candidate=raw_candidate,
        )


class CancelSkillImprovementProposal:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, proposal_id: SkillImprovementProposalId) -> SkillImprovementProposal:
        with self._uow_factory() as uow:
            proposal = uow.skill_improvement_proposals.get(proposal_id)
            if proposal is None:
                raise EntityConflict("skill improvement proposal not found")
            if proposal.status in {
                SkillProposalStatus.PROMOTED,
                SkillProposalStatus.CANCELLED,
                SkillProposalStatus.REJECTED,
            }:
                raise EntityConflict("proposal is already closed")
            cancelled = replace(proposal, status=SkillProposalStatus.CANCELLED)
            uow.skill_improvement_proposals.save(cancelled)
            uow.commit()
            return cancelled

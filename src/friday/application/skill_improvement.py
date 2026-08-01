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
from friday.domain.skill_evidence_snapshot import evidence_ids_from_payload, evidence_payload_hash
from friday.domain.skill_improvement_policy import CANONICAL_GENERATOR_VERSION

GENERATOR_CONFIG_FINGERPRINT = CANONICAL_GENERATOR_VERSION
MAX_CANDIDATE_PROMPT_CHARS = 120_000
MAX_CANDIDATE_RESPONSE_CHARS = 40_000


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
    snapshot_id: SkillEvidenceSnapshotId | None = None
    snapshot_payload: JsonValue = None
    base_revision_id: SkillRevisionId | None = None
    base_content_sha256: str | None = None
    generator_config_fingerprint: str = GENERATOR_CONFIG_FINGERPRINT

    def __post_init__(self) -> None:
        if not self.base_instructions or len(self.base_instructions) > 32_000:
            raise DomainValidationError("candidate base instructions must be bounded")
        if (
            len(self.evidence_snapshot_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_snapshot_hash)
            or not 1 <= self.max_response_chars <= MAX_CANDIDATE_RESPONSE_CHARS
        ):
            raise DomainValidationError("candidate generation request is invalid")
        if len(self.evidence_ids) > 200 or any(len(item) > 128 for item in self.evidence_ids):
            raise DomainValidationError("candidate evidence package is bounded")
        prompt_payload = {
            "base_instructions": self.base_instructions,
            "base_revision_id": str(self.base_revision_id) if self.base_revision_id else None,
            "base_content_sha256": self.base_content_sha256,
            "evidence_snapshot_id": str(self.snapshot_id) if self.snapshot_id else None,
            "evidence_snapshot": self.snapshot_payload,
            "evidence_snapshot_hash": self.evidence_snapshot_hash,
            "evidence_ids": self.evidence_ids,
            "feedback_summaries": self.feedback_summaries,
            "evaluator_summaries": self.evaluator_summaries,
            "generator_config_fingerprint": self.generator_config_fingerprint,
        }
        if (
            len(json.dumps(prompt_payload, sort_keys=True, separators=(",", ":")))
            > MAX_CANDIDATE_PROMPT_CHARS
        ):
            raise DomainValidationError("candidate generation prompt is too large")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise DomainValidationError("candidate evidence IDs must be unique")
        if (
            self.snapshot_id is None
            or self.base_revision_id is None
            or self.base_content_sha256 is None
            or self.snapshot_payload is None
        ):
            raise DomainValidationError("candidate generation provenance is incomplete")
        if (
            self.base_content_sha256
            != hashlib.sha256(self.base_instructions.encode("utf-8")).hexdigest()
        ):
            raise DomainValidationError("candidate base revision integrity failed")
        if self.generator_config_fingerprint != GENERATOR_CONFIG_FINGERPRINT:
            raise DomainValidationError("candidate generator configuration is not code-owned")
        if evidence_payload_hash(self.snapshot_payload) != self.evidence_snapshot_hash:
            raise DomainValidationError("candidate evidence snapshot integrity failed")
        if set(self.evidence_ids) != set(evidence_ids_from_payload(self.snapshot_payload)):
            raise DomainValidationError("candidate evidence IDs do not match the snapshot")


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
        with self._uow_factory() as uow:
            if generator_version != GENERATOR_CONFIG_FINGERPRINT:
                raise EntityConflict("candidate generator provenance is not code-owned")
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
            # The persisted snapshot, not a caller-supplied allow-list, is the
            # sole authority for candidate evidence references.
            frozen_evidence_ids = set(evidence_ids_from_payload(snapshot.evidence))
            candidate = parse_candidate_output(raw_candidate, frozen_evidence_ids)
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
        if generator_version != GENERATOR_CONFIG_FINGERPRINT:
            raise EntityConflict("candidate generator provenance is not code-owned")
        with self._create_proposal._uow_factory() as uow:
            snapshot = uow.skill_evidence_snapshots.get(evidence_snapshot_id)
            base = uow.skill_revisions.get(base_revision_id)
            if snapshot is None or base is None:
                raise EntityConflict("candidate generation inputs were not found")
            if (
                snapshot.skill_id != skill_id
                or snapshot.base_revision_id != base_revision_id
                or snapshot.content_sha256 != evidence_snapshot_hash
            ):
                raise EntityConflict("candidate generation inputs do not match the frozen snapshot")
            if base.instructions != base_instructions:
                raise EntityConflict("candidate base instructions do not match the persisted base")
            snapshot_payload = snapshot.evidence
            frozen_evidence_ids = set(evidence_ids_from_payload(snapshot_payload))
            frozen_base_hash = base.content_sha256
        raw_candidate = self._generator.generate_candidate(
            CandidateGenerationRequest(
                base_instructions=base_instructions,
                evidence_snapshot_hash=evidence_snapshot_hash,
                evidence_ids=tuple(sorted(frozen_evidence_ids)),
                feedback_summaries=feedback_summaries,
                evaluator_summaries=evaluator_summaries,
                snapshot_id=evidence_snapshot_id,
                snapshot_payload=snapshot_payload,
                base_revision_id=base_revision_id,
                base_content_sha256=frozen_base_hash,
            )
        )
        return self._create_proposal.execute(
            skill_id=skill_id,
            base_revision_id=base_revision_id,
            trigger_kind=trigger_kind,
            evidence_snapshot_id=evidence_snapshot_id,
            evidence_snapshot_hash=evidence_snapshot_hash,
            evidence_ids=frozen_evidence_ids,
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

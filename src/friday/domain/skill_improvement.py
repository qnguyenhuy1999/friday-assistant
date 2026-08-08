"""Brain-suggested candidates are inert proposals, never live Skill revisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    SkillEvidenceSnapshotId,
    SkillId,
    SkillImprovementProposalId,
    SkillRevisionId,
)
from friday.domain.skill import validate_skill_instructions
from friday.domain.time import ensure_utc


class SkillProposalStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_EVALUATION = "ready_for_evaluation"
    EVALUATING = "evaluating"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PROMOTED = "promoted"


@dataclass(frozen=True, slots=True)
class SkillImprovementProposal:
    id: SkillImprovementProposalId
    skill_id: SkillId
    base_revision_id: SkillRevisionId
    status: SkillProposalStatus
    trigger_kind: str
    evidence_snapshot_id: SkillEvidenceSnapshotId
    evidence_snapshot_hash: str
    proposed_instructions: str
    proposed_content_sha256: str
    rationale: str
    generator_version: str
    candidate_prompt_version: str
    candidate_prompt_sha256: str
    created_at: datetime

    def __post_init__(self) -> None:
        instructions = validate_skill_instructions(self.proposed_instructions)
        if not self.trigger_kind or not self.generator_version or len(self.rationale) > 8000:
            raise DomainValidationError("proposal metadata must be present and bounded")
        if not self.candidate_prompt_version or len(self.candidate_prompt_version) > 128:
            raise DomainValidationError("proposal candidate prompt version must be bounded")
        if len(self.evidence_snapshot_hash) != 64:
            raise DomainValidationError("proposal evidence snapshot must be sha256")
        if len(self.candidate_prompt_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.candidate_prompt_sha256
        ):
            raise DomainValidationError("proposal candidate prompt must be sha256")
        digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        if self.proposed_content_sha256 != digest:
            raise DomainValidationError("proposal content hash does not match instructions")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

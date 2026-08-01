"""Exact human-authorized promotion and rollback requests for Skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    SkillCandidateEvaluationId,
    SkillId,
    SkillImprovementProposalId,
    SkillPromotionRequestId,
    SkillRevisionId,
    SkillRollbackRequestId,
)
from friday.domain.time import ensure_utc


class PromotionRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    PROMOTED = "promoted"


class RollbackRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STALE = "stale"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class SkillPromotionRequest:
    id: SkillPromotionRequestId
    proposal_id: SkillImprovementProposalId
    skill_id: SkillId
    base_revision_id: SkillRevisionId
    expected_active_revision_id: SkillRevisionId
    candidate_sha256: str
    candidate_evaluation_id: SkillCandidateEvaluationId
    comparison_report_sha256: str
    target_version: int
    authorization_fingerprint: str
    status: PromotionRequestStatus
    created_at: datetime
    resolved_at: datetime | None = None
    resolver: str | None = None
    promoted_revision_id: SkillRevisionId | None = None

    def __post_init__(self) -> None:
        for value in (
            self.candidate_sha256,
            self.comparison_report_sha256,
            self.authorization_fingerprint,
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise DomainValidationError("promotion fingerprints must be sha256 hex")
        if self.target_version < 1:
            raise DomainValidationError("promotion target version must be positive")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if self.resolved_at is not None:
            object.__setattr__(self, "resolved_at", ensure_utc(self.resolved_at))


@dataclass(frozen=True, slots=True)
class SkillRollbackRequest:
    id: SkillRollbackRequestId
    skill_id: SkillId
    expected_current_revision_id: SkillRevisionId
    target_revision_id: SkillRevisionId
    reason: str
    authorization_fingerprint: str
    status: RollbackRequestStatus
    created_at: datetime
    resolved_at: datetime | None = None
    resolver: str | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip() or len(self.reason) > 4000:
            raise DomainValidationError("rollback reason must be present and bounded")
        if len(self.authorization_fingerprint) != 64:
            raise DomainValidationError("rollback authorization fingerprint must be sha256")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if self.resolved_at is not None:
            object.__setattr__(self, "resolved_at", ensure_utc(self.resolved_at))

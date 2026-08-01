"""Durable progress for one safe Skill-improvement orchestration item."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    SkillId,
    SkillImprovementProposalId,
    SkillImprovementWorkId,
)
from friday.domain.time import ensure_utc


class SkillImprovementWorkState(StrEnum):
    EVIDENCE_SELECTION = "evidence_selection"
    CANDIDATE_GENERATION = "candidate_generation"
    BASELINE_EVALUATION = "baseline_evaluation"
    CANDIDATE_EVALUATION = "candidate_evaluation"
    COMPARISON = "comparison"
    READY_FOR_REVIEW = "ready_for_review"
    FAILED = "failed"
    COMPLETE = "complete"


ACTIVE_IMPROVEMENT_WORK_STATES = frozenset(
    {
        SkillImprovementWorkState.EVIDENCE_SELECTION,
        SkillImprovementWorkState.CANDIDATE_GENERATION,
        SkillImprovementWorkState.BASELINE_EVALUATION,
        SkillImprovementWorkState.CANDIDATE_EVALUATION,
        SkillImprovementWorkState.COMPARISON,
    }
)


@dataclass(frozen=True, slots=True)
class SkillImprovementWork:
    id: SkillImprovementWorkId
    skill_id: SkillId
    state: SkillImprovementWorkState
    proposal_id: SkillImprovementProposalId | None
    attempt_count: int
    next_attempt_at: datetime
    claimed_by: str | None
    claim_token: str | None
    claim_generation: int
    lease_expires_at: datetime | None
    failure_code: str | None
    failure_detail: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.attempt_count < 0 or self.claim_generation < 0:
            raise DomainValidationError("improvement work counters must be non-negative")
        if self.failure_code is not None and (
            not self.failure_code
            or len(self.failure_code) > 128
            or any(
                char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in self.failure_code
            )
        ):
            raise DomainValidationError("improvement work failure code is invalid")
        if self.failure_detail is not None and len(self.failure_detail) > 1000:
            raise DomainValidationError("improvement work failure detail is too long")
        object.__setattr__(self, "next_attempt_at", ensure_utc(self.next_attempt_at))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))
        if self.lease_expires_at is not None:
            object.__setattr__(self, "lease_expires_at", ensure_utc(self.lease_expires_at))

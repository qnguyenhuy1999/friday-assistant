"""Immutable factual evidence produced by terminal runs using frozen skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    RunId,
    SkillId,
    SkillRevisionId,
    SkillRunFeedbackId,
    SkillUsageRecordId,
    TaskId,
)
from friday.domain.time import ensure_utc


class SkillUsageOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESOLUTION_FAILED = "resolution_failed"


class SkillFeedbackRating(StrEnum):
    HELPFUL = "helpful"
    NEUTRAL = "neutral"
    HARMFUL = "harmful"


@dataclass(frozen=True, slots=True)
class SkillUsageRecord:
    id: SkillUsageRecordId
    run_id: RunId
    task_id: TaskId
    skill_id: SkillId
    revision_id: SkillRevisionId
    position: int
    resolution_id: str
    execution_id: RunId
    attempt_number: int
    started_at: datetime | None
    completed_at: datetime
    outcome: SkillUsageOutcome
    failure_code: str | None
    tool_call_count: int
    approval_count: int
    duration_ms: int | None
    created_at: datetime

    def __post_init__(self) -> None:
        if self.position < 1 or self.attempt_number < 1:
            raise DomainValidationError("skill usage position and attempt must be positive")
        if self.tool_call_count < 0 or self.approval_count < 0:
            raise DomainValidationError("skill usage counts must be non-negative")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise DomainValidationError("skill usage duration must be non-negative")
        if self.outcome is SkillUsageOutcome.FAILED and not self.failure_code:
            raise DomainValidationError("failed skill usage requires a failure code")
        if self.outcome is not SkillUsageOutcome.FAILED and self.failure_code is not None:
            raise DomainValidationError("only failed skill usage may have a failure code")
        started_at = ensure_utc(self.started_at) if self.started_at else None
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "completed_at", ensure_utc(self.completed_at))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class SkillRunFeedback:
    id: SkillRunFeedbackId
    run_id: RunId
    skill_id: SkillId
    revision_id: SkillRevisionId
    rating: SkillFeedbackRating
    note: str
    created_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.created_by.strip() or len(self.created_by) > 128:
            raise DomainValidationError("feedback created_by must be present and bounded")
        if len(self.note) > 4000:
            raise DomainValidationError("feedback note must be bounded")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

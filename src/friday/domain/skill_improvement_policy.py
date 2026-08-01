"""Durable, operator-owned trigger thresholds for safe Skill improvement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import SkillEvaluationSuiteId, SkillId
from friday.domain.time import ensure_utc


@dataclass(frozen=True, slots=True)
class SkillImprovementPolicy:
    skill_id: SkillId
    enabled: bool
    minimum_usage_records: int
    minimum_failures: int
    minimum_harmful_feedback: int
    evaluation_suite_id: SkillEvaluationSuiteId
    cooldown_seconds: int
    max_open_proposals: int
    evidence_window_size: int
    generator_version: str
    comparison_policy_version: str
    created_at: datetime
    updated_at: datetime
    last_triggered_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            min(
                self.minimum_usage_records,
                self.minimum_failures,
                self.minimum_harmful_feedback,
                self.cooldown_seconds,
            )
            < 0
            or self.max_open_proposals < 1
            or not 1 <= self.evidence_window_size <= 200
        ):
            raise DomainValidationError("invalid skill improvement policy thresholds")
        if not self.generator_version or not self.comparison_policy_version:
            raise DomainValidationError("improvement policy versions are required")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))
        if self.last_triggered_at is not None:
            object.__setattr__(self, "last_triggered_at", ensure_utc(self.last_triggered_at))

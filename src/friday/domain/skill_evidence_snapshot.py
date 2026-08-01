"""Bounded immutable evidence selections used by improvement proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import SkillEvidenceSnapshotId, SkillId, SkillRevisionId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc


@dataclass(frozen=True, slots=True)
class SkillEvidenceSnapshot:
    id: SkillEvidenceSnapshotId
    skill_id: SkillId
    base_revision_id: SkillRevisionId
    evidence: JsonValue
    content_sha256: str
    created_at: datetime

    @classmethod
    def new(
        cls,
        *,
        id: SkillEvidenceSnapshotId,
        skill_id: SkillId,
        base_revision_id: SkillRevisionId,
        evidence: JsonValue,
        created_at: datetime,
    ) -> SkillEvidenceSnapshot:
        normalized = ensure_json_value(evidence)
        payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > 64_000:
            raise DomainValidationError("evidence snapshot exceeds the bounded package size")
        return cls(
            id,
            skill_id,
            base_revision_id,
            normalized,
            hashlib.sha256(payload).hexdigest(),
            ensure_utc(created_at),
        )

    def __post_init__(self) -> None:
        if len(self.content_sha256) != 64:
            raise DomainValidationError("evidence snapshot hash must be sha256")
        object.__setattr__(self, "evidence", ensure_json_value(self.evidence))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

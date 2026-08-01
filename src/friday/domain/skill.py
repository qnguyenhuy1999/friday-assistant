"""Durable, operator-owned behavioral knowledge.  Skills confer no authority."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import (
    RunId,
    RunSkillResolutionId,
    SkillId,
    SkillRevisionId,
    TaskId,
)
from friday.domain.time import ensure_utc

MAX_SKILL_KEY_LENGTH = 96
MAX_SKILL_INSTRUCTIONS_LENGTH = 32_000
_KEY = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")


class SkillStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class SkillRevisionSourceKind(StrEnum):
    OPERATOR = "operator"
    IMPORTED = "imported"
    GENERATED = "generated"


def validate_skill_key(value: str) -> str:
    if (
        not value
        or len(value) > MAX_SKILL_KEY_LENGTH
        or value != value.lower()
        or not _KEY.fullmatch(value)
    ):
        raise DomainValidationError(
            "Skill.key must be lowercase dot-or-hyphen separated machine identity"
        )
    return value


def validate_skill_instructions(value: str) -> str:
    if not value or len(value) > MAX_SKILL_INSTRUCTIONS_LENGTH:
        raise DomainValidationError(
            "SkillRevision.instructions must be non-empty and within the maximum length"
        )
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise DomainValidationError("SkillRevision.instructions must be UTF-8 encodable") from exc
    if any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value):
        raise DomainValidationError(
            "SkillRevision.instructions contains disallowed control content"
        )
    return value


@dataclass(frozen=True, slots=True)
class SkillRevision:
    id: SkillRevisionId
    skill_id: SkillId
    version: int
    instructions: str
    content_sha256: str
    source_kind: SkillRevisionSourceKind
    created_at: datetime
    promotion_request_id: str | None = None

    def __post_init__(self) -> None:
        # Instructions are immutable persisted authority.  Rechecking the
        # digest while reconstructing the domain object makes a corrupt row
        # fail closed before it can reach a runtime, evaluator, or API.
        validate_skill_instructions(self.instructions)
        if self.content_sha256 != hashlib.sha256(self.instructions.encode("utf-8")).hexdigest():
            raise DomainValidationError("skill_integrity_failed")
        if len(self.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_sha256
        ):
            raise DomainValidationError("SkillRevision.content_sha256 must be lowercase sha256")
        if (
            self.source_kind is SkillRevisionSourceKind.GENERATED
            and self.promotion_request_id is None
        ):
            raise DomainValidationError(
                "generated SkillRevision requires an approved promotion request"
            )
        if self.source_kind is not SkillRevisionSourceKind.GENERATED and self.promotion_request_id:
            raise DomainValidationError(
                "only generated SkillRevisions may carry promotion provenance"
            )
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def new(
        cls,
        *,
        id: SkillRevisionId,
        skill_id: SkillId,
        version: int,
        instructions: str,
        source_kind: SkillRevisionSourceKind,
        created_at: datetime,
        promotion_request_id: str | None = None,
    ) -> SkillRevision:
        if version < 1:
            raise DomainValidationError("SkillRevision.version must be positive")
        if source_kind is SkillRevisionSourceKind.GENERATED and promotion_request_id is None:
            raise DomainValidationError(
                "generated SkillRevision requires an approved promotion request"
            )
        if (
            source_kind is not SkillRevisionSourceKind.GENERATED
            and promotion_request_id is not None
        ):
            raise DomainValidationError(
                "only generated SkillRevisions may carry promotion provenance"
            )
        instructions = validate_skill_instructions(instructions)
        return cls(
            id,
            skill_id,
            version,
            instructions,
            hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            source_kind,
            ensure_utc(created_at),
            promotion_request_id,
        )


MAX_SKILLS_PER_TASK = 16


@dataclass(frozen=True, slots=True)
class TaskSkillBinding:
    task_id: TaskId
    skill_id: SkillId
    position: int
    created_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= self.position <= MAX_SKILLS_PER_TASK:
            raise DomainValidationError("TaskSkillBinding.position is out of range")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class RunSkillResolution:
    id: RunSkillResolutionId
    run_id: RunId
    resolved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolved_at", ensure_utc(self.resolved_at))


@dataclass(frozen=True, slots=True)
class RunSkillBinding:
    run_id: RunId
    skill_id: SkillId
    revision_id: SkillRevisionId
    position: int

    def __post_init__(self) -> None:
        if not 1 <= self.position <= MAX_SKILLS_PER_TASK:
            raise DomainValidationError("RunSkillBinding.position is out of range")


@dataclass(slots=True)
class Skill:
    _id: SkillId
    _key: str
    _display_name: str
    _description: str
    _status: SkillStatus
    _active_revision_id: SkillRevisionId | None
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def new(
        cls, *, id: SkillId, key: str, display_name: str, description: str, created_at: datetime
    ) -> Skill:
        if not display_name.strip():
            raise DomainValidationError("Skill.display_name must not be empty")
        now = ensure_utc(created_at)
        return cls(
            id,
            validate_skill_key(key),
            display_name.strip(),
            description.strip(),
            SkillStatus.ACTIVE,
            None,
            now,
            now,
        )

    @property
    def id(self) -> SkillId:
        return self._id

    @property
    def key(self) -> str:
        return self._key

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def description(self) -> str:
        return self._description

    @property
    def status(self) -> SkillStatus:
        return self._status

    @property
    def active_revision_id(self) -> SkillRevisionId | None:
        return self._active_revision_id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def activate(self, revision: SkillRevision, at: datetime) -> None:
        if self._status is SkillStatus.ARCHIVED:
            raise InvalidStateTransition("Skill", self._status.value, "activate")
        if revision.skill_id != self.id:
            raise DomainValidationError("Skill revision does not belong to skill")
        self._active_revision_id, self._updated_at = revision.id, ensure_utc(at)

    def disable(self, at: datetime) -> None:
        if self._status is SkillStatus.ARCHIVED:
            raise InvalidStateTransition("Skill", self._status.value, "disabled")
        self._status, self._updated_at = SkillStatus.DISABLED, ensure_utc(at)

    def archive(self, at: datetime) -> None:
        if self._status is SkillStatus.ARCHIVED:
            return
        self._status, self._updated_at = SkillStatus.ARCHIVED, ensure_utc(at)

"""Durable, Friday-owned Agent identity.  An Agent revision confers no
authority: it may describe persona instructions, a reasoning role, a
preferred brain runtime kind, and bounded runtime configuration, but it
grants no approval bypass, no tool/filesystem/shell/computer/MCP/messaging
authority. Execution authority always remains Friday-owned and flows only
through AgentRunProcessor -> ToolGateway -> approval."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import (
    AgentId,
    AgentRevisionId,
    RunAgentResolutionId,
    RunId,
    TaskId,
)
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc

MAX_AGENT_KEY_LENGTH = 96
MAX_AGENT_INSTRUCTIONS_LENGTH = 32_000
MAX_RUNTIME_KIND_LENGTH = 64
_KEY = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\Z")
_RUNTIME_KIND = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class AgentStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class AgentRevisionSourceKind(StrEnum):
    OPERATOR = "operator"
    IMPORTED = "imported"


def validate_agent_key(value: str) -> str:
    if (
        not value
        or len(value) > MAX_AGENT_KEY_LENGTH
        or value != value.lower()
        or not _KEY.fullmatch(value)
    ):
        raise DomainValidationError(
            "Agent.key must be lowercase dot-or-hyphen separated machine identity"
        )
    return value


def validate_agent_instructions(value: str) -> str:
    if not value or len(value) > MAX_AGENT_INSTRUCTIONS_LENGTH:
        raise DomainValidationError(
            "AgentRevision.instructions must be non-empty and within the maximum length"
        )
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise DomainValidationError("AgentRevision.instructions must be UTF-8 encodable") from exc
    if any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in value):
        raise DomainValidationError(
            "AgentRevision.instructions contains disallowed control content"
        )
    return value


def validate_runtime_kind(value: str) -> str:
    if not value or len(value) > MAX_RUNTIME_KIND_LENGTH or not _RUNTIME_KIND.fullmatch(value):
        raise DomainValidationError(
            "AgentRevision.runtime_kind must be a lowercase machine identity"
        )
    return value


def _canonical_revision_content(
    *, instructions: str, runtime_kind: str, runtime_config: JsonValue
) -> str:
    """Canonical bytes the revision digest is computed over. Field-separated
    (not naive concatenation) so no combination of instructions/runtime_kind
    text can be crafted to collide with a different split of the same
    material."""
    payload = {
        "instructions": instructions,
        "runtime_kind": runtime_kind,
        "runtime_config": runtime_config,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class AgentRevision:
    id: AgentRevisionId
    agent_id: AgentId
    version: int
    instructions: str
    runtime_kind: str
    runtime_config: JsonValue
    content_sha256: str
    source_kind: AgentRevisionSourceKind
    created_at: datetime

    def __post_init__(self) -> None:
        # Recheck the digest while reconstructing the domain object so a
        # corrupt row fails closed before it can reach a runtime or API.
        validate_agent_instructions(self.instructions)
        validate_runtime_kind(self.runtime_kind)
        ensure_json_value(self.runtime_config, path="AgentRevision.runtime_config")
        expected = hashlib.sha256(
            _canonical_revision_content(
                instructions=self.instructions,
                runtime_kind=self.runtime_kind,
                runtime_config=self.runtime_config,
            ).encode("utf-8")
        ).hexdigest()
        if self.content_sha256 != expected:
            raise DomainValidationError("agent_integrity_failed")
        if len(self.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_sha256
        ):
            raise DomainValidationError("AgentRevision.content_sha256 must be lowercase sha256")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def new(
        cls,
        *,
        id: AgentRevisionId,
        agent_id: AgentId,
        version: int,
        instructions: str,
        runtime_kind: str,
        runtime_config: JsonValue,
        source_kind: AgentRevisionSourceKind,
        created_at: datetime,
    ) -> AgentRevision:
        if version < 1:
            raise DomainValidationError("AgentRevision.version must be positive")
        instructions = validate_agent_instructions(instructions)
        runtime_kind = validate_runtime_kind(runtime_kind)
        runtime_config = ensure_json_value(runtime_config, path="AgentRevision.runtime_config")
        content_sha256 = hashlib.sha256(
            _canonical_revision_content(
                instructions=instructions,
                runtime_kind=runtime_kind,
                runtime_config=runtime_config,
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            id,
            agent_id,
            version,
            instructions,
            runtime_kind,
            runtime_config,
            content_sha256,
            source_kind,
            ensure_utc(created_at),
        )


@dataclass(frozen=True, slots=True)
class TaskAgentBinding:
    task_id: TaskId
    agent_id: AgentId
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class RunAgentResolution:
    id: RunAgentResolutionId
    run_id: RunId
    agent_id: AgentId
    revision_id: AgentRevisionId
    resolved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolved_at", ensure_utc(self.resolved_at))


@dataclass(slots=True)
class Agent:
    _id: AgentId
    _key: str
    _display_name: str
    _description: str
    _status: AgentStatus
    _active_revision_id: AgentRevisionId | None
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def new(
        cls, *, id: AgentId, key: str, display_name: str, description: str, created_at: datetime
    ) -> Agent:
        if not display_name.strip():
            raise DomainValidationError("Agent.display_name must not be empty")
        now = ensure_utc(created_at)
        return cls(
            id,
            validate_agent_key(key),
            display_name.strip(),
            description.strip(),
            AgentStatus.ACTIVE,
            None,
            now,
            now,
        )

    @property
    def id(self) -> AgentId:
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
    def status(self) -> AgentStatus:
        return self._status

    @property
    def active_revision_id(self) -> AgentRevisionId | None:
        return self._active_revision_id

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    def activate(self, revision: AgentRevision, at: datetime) -> None:
        if self._status is AgentStatus.ARCHIVED:
            raise InvalidStateTransition("Agent", self._status.value, "activate")
        if revision.agent_id != self.id:
            raise DomainValidationError("Agent revision does not belong to agent")
        self._active_revision_id, self._updated_at = revision.id, ensure_utc(at)

    def disable(self, at: datetime) -> None:
        if self._status is AgentStatus.ARCHIVED:
            raise InvalidStateTransition("Agent", self._status.value, "disabled")
        self._status, self._updated_at = AgentStatus.DISABLED, ensure_utc(at)

    def archive(self, at: datetime) -> None:
        if self._status is AgentStatus.ARCHIVED:
            return
        self._status, self._updated_at = AgentStatus.ARCHIVED, ensure_utc(at)

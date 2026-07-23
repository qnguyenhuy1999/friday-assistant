"""Artifact: metadata for a produced output or referenced external resource.

Metadata only — an Artifact never reads/writes files, verifies URLs, or
manages object storage; it records where a resource lives, not its bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import ArtifactId, RunId, RunStepId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc


class ArtifactKind(StrEnum):
    TEXT = "text"
    FILE = "file"
    DIRECTORY = "directory"
    URL = "url"
    JSON = "json"
    IMAGE = "image"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Artifact:
    id: ArtifactId
    run_id: RunId
    kind: ArtifactKind
    name: str
    media_type: str
    location: str
    created_at: datetime
    step_id: RunStepId | None = field(default=None)
    size: int | None = field(default=None)
    checksum: str | None = field(default=None)
    metadata: JsonValue = field(default=None)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainValidationError("Artifact.name must not be empty")
        if not self.location.strip():
            raise DomainValidationError("Artifact.location must not be empty")
        if self.size is not None and self.size < 0:
            raise DomainValidationError("Artifact.size must not be negative")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(
            self, "metadata", ensure_json_value(self.metadata, path="Artifact.metadata")
        )

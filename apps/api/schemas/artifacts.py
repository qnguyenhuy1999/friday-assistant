"""Artifact request/response models and application-layer mapping."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from apps.api.pagination import Page
from friday.application.commands import RecordArtifactCommand
from friday.application.results import ArtifactResult
from friday.domain.artifact import ArtifactKind
from friday.domain.identifiers import ArtifactId, RunId, RunStepId


class RecordArtifactBody(BaseModel):
    kind: ArtifactKind
    name: str
    media_type: str
    location: str
    step_id: str | None = None
    size: int | None = None
    checksum: str | None = None
    metadata: Any = None
    artifact_id: str | None = None

    def to_command(self, run_id: RunId) -> RecordArtifactCommand:
        return RecordArtifactCommand(
            run_id=run_id,
            kind=self.kind,
            name=self.name,
            media_type=self.media_type,
            location=self.location,
            step_id=RunStepId.parse(self.step_id) if self.step_id is not None else None,
            size=self.size,
            checksum=self.checksum,
            metadata=self.metadata,
            artifact_id=ArtifactId.parse(self.artifact_id)
            if self.artifact_id is not None
            else None,
        )


class ArtifactResponse(BaseModel):
    artifact_id: str
    run_id: str
    step_id: str | None
    kind: ArtifactKind
    name: str
    media_type: str
    location: str
    created_at: datetime
    size: int | None
    checksum: str | None
    metadata: Any

    @classmethod
    def from_result(cls, result: ArtifactResult) -> ArtifactResponse:
        return cls(
            artifact_id=str(result.artifact_id),
            run_id=str(result.run_id),
            step_id=str(result.step_id) if result.step_id is not None else None,
            kind=result.kind,
            name=result.name,
            media_type=result.media_type,
            location=result.location,
            created_at=result.created_at,
            size=result.size,
            checksum=result.checksum,
            metadata=result.metadata,
        )


class ArtifactPage(BaseModel):
    items: list[ArtifactResponse]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: Page[ArtifactResult]) -> ArtifactPage:
        return cls(
            items=[ArtifactResponse.from_result(r) for r in page.items],
            next_cursor=page.next_cursor,
        )

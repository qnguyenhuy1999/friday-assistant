"""Artifact use cases: record and read produced-output metadata.

Metadata only — no object storage, upload, download, checksum computation,
or filesystem access happens here. The checksum, size, and location are
recorded exactly as the caller supplies them.

Identity policy: an Artifact is identified by its ID alone. Matching
checksum/location never implies the same Artifact. `RecordArtifact` with a
caller-supplied ID replays idempotently when every recorded field matches;
the same ID with different data is a conflict.
"""

from __future__ import annotations

from friday.application.commands import RecordArtifactCommand
from friday.application.errors import (
    ArtifactNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.results import ArtifactResult
from friday.domain.artifact import Artifact
from friday.domain.event import RunEventType
from friday.domain.identifiers import ArtifactId, RunId
from friday.domain.run import RunStatus


def artifact_result(artifact: Artifact) -> ArtifactResult:
    return ArtifactResult(
        artifact.id,
        artifact.run_id,
        artifact.step_id,
        artifact.kind,
        artifact.name,
        artifact.media_type,
        artifact.location,
        artifact.created_at,
        artifact.size,
        artifact.checksum,
        artifact.metadata,
    )


class GetArtifact(LifecycleEvents):
    def execute(self, artifact_id: ArtifactId) -> ArtifactResult:
        with self._uow_factory() as uow:
            artifact = uow.artifacts.get(artifact_id)
            if artifact is None:
                raise ArtifactNotFound(artifact_id)
            return artifact_result(artifact)


class ListArtifactsForRun(LifecycleEvents):
    def execute(self, run_id: RunId) -> list[ArtifactResult]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return [artifact_result(a) for a in uow.artifacts.list_for_run(run_id)]


def _same_recording(existing: Artifact, candidate: Artifact) -> bool:
    """Identical data ignoring created_at, which the replay never controls."""
    return (
        existing.run_id == candidate.run_id
        and existing.step_id == candidate.step_id
        and existing.kind == candidate.kind
        and existing.name == candidate.name
        and existing.media_type == candidate.media_type
        and existing.location == candidate.location
        and existing.size == candidate.size
        and existing.checksum == candidate.checksum
        and existing.metadata == candidate.metadata
    )


class RecordArtifact(LifecycleEvents):
    def execute(self, command: RecordArtifactCommand) -> ArtifactResult:
        with self._uow_factory() as uow:
            run = uow.runs.get(command.run_id)
            if run is None:
                raise RunNotFound(command.run_id)
            if run.status is not RunStatus.RUNNING:
                raise EntityConflict("run cannot record artifacts")
            if command.step_id is not None:
                step = uow.steps.get(command.step_id)
                if step is None:
                    raise RunStepNotFound(command.step_id)
                if step.run_id != run.id:
                    raise EntityConflict("step does not belong to run")
            now = self._clock.now()
            artifact = Artifact(
                id=command.artifact_id or ArtifactId.new(),
                run_id=run.id,
                kind=command.kind,
                name=command.name,
                media_type=command.media_type,
                location=command.location,
                created_at=now,
                step_id=command.step_id,
                size=command.size,
                checksum=command.checksum,
                metadata=command.metadata,
            )
            if command.artifact_id is not None:
                existing = uow.artifacts.get(command.artifact_id)
                if existing is not None:
                    if not _same_recording(existing, artifact):
                        raise EntityConflict("artifact identity is immutable")
                    uow.commit()
                    return artifact_result(existing)
            uow.artifacts.add(artifact)
            self.append_run_events(
                uow,
                run,
                now,
                [
                    (
                        RunEventType.ARTIFACT_CREATED,
                        {
                            "artifact_id": str(artifact.id),
                            "kind": artifact.kind.value,
                            "location": artifact.location,
                        },
                        artifact.step_id,
                    )
                ],
            )
            uow.commit()
            return artifact_result(artifact)

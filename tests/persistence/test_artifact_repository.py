from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from friday.domain import Artifact, ArtifactId, ArtifactKind, Run, RunId, Task, TaskId
from friday.infrastructure.persistence.repositories import (
    ArtifactRepository,
    RunRepository,
    TaskRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(session: Session) -> RunId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return run.id


def _make_artifact(run_id: RunId, created_at: datetime) -> Artifact:
    return Artifact(
        id=ArtifactId.new(),
        run_id=run_id,
        kind=ArtifactKind.TEXT,
        name="n",
        media_type="text/plain",
        location="loc",
        created_at=created_at,
    )


def test_add_then_get_round_trips(session: Session) -> None:
    run_id = _make_run(session)
    repo = ArtifactRepository(session)
    artifact = _make_artifact(run_id, T0)
    repo.add(artifact)
    session.flush()
    fetched = repo.get(artifact.id)
    assert fetched is not None
    assert fetched.id == artifact.id
    assert fetched.run_id == artifact.run_id


def test_get_returns_none_for_missing_id(session: Session) -> None:
    repo = ArtifactRepository(session)
    assert repo.get(ArtifactId.new()) is None


def test_has_no_save_method() -> None:
    assert not hasattr(ArtifactRepository, "save")


def test_list_for_run_orders_by_created_at_then_id(session: Session) -> None:
    run_id = _make_run(session)
    repo = ArtifactRepository(session)
    artifact_b = _make_artifact(run_id, T0 + timedelta(seconds=1))
    artifact_a = _make_artifact(run_id, T0)
    repo.add(artifact_b)
    repo.add(artifact_a)
    session.flush()
    result = repo.list_for_run(run_id)
    assert [a.id for a in result] == [artifact_a.id, artifact_b.id]

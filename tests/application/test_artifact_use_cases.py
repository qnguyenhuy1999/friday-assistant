"""Artifact use cases: ownership validation, metadata preservation,
ID-based duplicate policy, deterministic reads."""

from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.artifact_use_cases import (
    GetArtifact,
    ListArtifactsForRun,
    RecordArtifact,
)
from friday.application.commands import RecordArtifactCommand
from friday.application.errors import (
    ArtifactNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.domain.artifact import ArtifactKind
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import ArtifactId, RunId, RunStepId, TaskId
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

T1 = T0 + timedelta(minutes=1)


def _prepared(
    *, with_step: bool = False
) -> tuple[FakeUnitOfWork, CountingUnitOfWorkFactory, Run, RunStep | None]:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    uow.task_repo.add(task)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    run.start(T0)
    uow.run_repo.add(run)
    step: RunStep | None = None
    if with_step:
        step = RunStep.new(id=RunStepId.new(), run_id=run.id, name="s", position=0, created_at=T0)
        step.start(T0)
        uow.step_repo.add(step)
    return uow, factory, run, step


def _command(run: Run, step: RunStep | None = None, **overrides: object) -> RecordArtifactCommand:
    defaults: dict[str, object] = {
        "run_id": run.id,
        "kind": ArtifactKind.FILE,
        "name": "out.log",
        "media_type": "text/plain",
        "location": "/tmp/out.log",
        "step_id": step.id if step else None,
        "size": 42,
        "checksum": "sha256:abc",
        "metadata": {"lines": 10},
    }
    defaults.update(overrides)
    return RecordArtifactCommand(**defaults)  # type: ignore[arg-type]


def test_record_run_owned_artifact_preserves_metadata_and_appends_event() -> None:
    uow, factory, run, _ = _prepared()
    result = RecordArtifact(factory, FakeClock(T1)).execute(_command(run))
    assert result.kind is ArtifactKind.FILE
    assert result.media_type == "text/plain"
    assert result.location == "/tmp/out.log"
    assert result.size == 42
    assert result.checksum == "sha256:abc"
    assert result.metadata == {"lines": 10}
    assert result.created_at == T1
    event = uow.event_store.appended[-1]
    assert event.type.value == "artifact_created"
    assert event.payload == {
        "artifact_id": str(result.artifact_id),
        "kind": "file",
        "location": "/tmp/out.log",
    }
    assert uow.commit_count == 1


def test_record_step_owned_artifact_tags_event_with_step() -> None:
    uow, factory, run, step = _prepared(with_step=True)
    assert step is not None
    result = RecordArtifact(factory, FakeClock(T1)).execute(_command(run, step))
    assert result.step_id == step.id
    assert uow.event_store.appended[-1].step_id == step.id


def test_record_rejects_missing_run_and_step_and_mismatch() -> None:
    uow, factory, run, _ = _prepared()
    with pytest.raises(RunNotFound):
        RecordArtifact(factory, FakeClock(T1)).execute(_command(run, run_id=RunId.new()))
    with pytest.raises(RunStepNotFound):
        RecordArtifact(factory, FakeClock(T1)).execute(_command(run, step_id=RunStepId.new()))
    foreign = RunStep.new(
        id=RunStepId.new(), run_id=RunId.new(), name="s", position=0, created_at=T0
    )
    uow.step_repo.add(foreign)
    with pytest.raises(EntityConflict):
        RecordArtifact(factory, FakeClock(T1)).execute(_command(run, step_id=foreign.id))
    assert uow.event_store.appended == []
    assert uow.artifact_repo.items == {}


def test_record_rejects_non_running_run() -> None:
    _, factory, run, _ = _prepared()
    run.succeed(T0)
    with pytest.raises(EntityConflict):
        RecordArtifact(factory, FakeClock(T1)).execute(_command(run))


def test_record_rejects_json_incompatible_metadata() -> None:
    _, factory, run, _ = _prepared()
    with pytest.raises(DomainValidationError):
        RecordArtifact(factory, FakeClock(T1)).execute(_command(run, metadata={"at": T0}))


def test_duplicate_id_identical_data_is_idempotent() -> None:
    uow, factory, run, _ = _prepared()
    artifact_id = ArtifactId.new()
    first = RecordArtifact(factory, FakeClock(T1)).execute(_command(run, artifact_id=artifact_id))
    events = len(uow.event_store.appended)
    replay = RecordArtifact(factory, FakeClock(T1 + timedelta(minutes=5))).execute(
        _command(run, artifact_id=artifact_id)
    )
    assert replay == first  # original created_at preserved, no duplicate row
    assert len(uow.event_store.appended) == events
    assert len(uow.artifact_repo.items) == 1


def test_duplicate_id_conflicting_data_is_a_conflict() -> None:
    uow, factory, run, _ = _prepared()
    artifact_id = ArtifactId.new()
    RecordArtifact(factory, FakeClock(T1)).execute(_command(run, artifact_id=artifact_id))
    events = len(uow.event_store.appended)
    with pytest.raises(EntityConflict):
        RecordArtifact(factory, FakeClock(T1)).execute(
            _command(run, artifact_id=artifact_id, checksum="sha256:other")
        )
    assert len(uow.event_store.appended) == events


def test_matching_content_under_new_id_is_a_distinct_artifact() -> None:
    uow, factory, run, _ = _prepared()
    first = RecordArtifact(factory, FakeClock(T1)).execute(_command(run))
    second = RecordArtifact(factory, FakeClock(T1)).execute(_command(run))
    assert first.artifact_id != second.artifact_id
    assert len(uow.artifact_repo.items) == 2


def test_get_and_list_reads() -> None:
    uow, factory, run, _ = _prepared()
    clock = FakeClock(T1)
    earlier = RecordArtifact(factory, clock).execute(_command(run, name="a"))
    later = RecordArtifact(factory, FakeClock(T1 + timedelta(minutes=1))).execute(
        _command(run, name="b")
    )
    assert GetArtifact(factory, clock).execute(earlier.artifact_id).name == "a"
    listed = ListArtifactsForRun(factory, clock).execute(run.id)
    assert [a.artifact_id for a in listed] == [earlier.artifact_id, later.artifact_id]
    assert uow.commit_count == 2  # reads never commit
    with pytest.raises(ArtifactNotFound):
        GetArtifact(factory, clock).execute(ArtifactId.new())
    with pytest.raises(RunNotFound):
        ListArtifactsForRun(factory, clock).execute(RunId.new())

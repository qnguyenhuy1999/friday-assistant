from __future__ import annotations

from datetime import UTC, datetime

from friday.domain import Run, RunId, RunStep, RunStepId, Task, TaskId
from friday.infrastructure.persistence.repositories import (
    RunRepository,
    RunStepRepository,
    TaskRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(session) -> RunId:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    return run.id


def test_add_then_get_round_trips(session) -> None:
    run_id = _make_run(session)
    repo = RunStepRepository(session)
    step = RunStep.new(id=RunStepId.new(), run_id=run_id, name="s", position=0, created_at=T0)
    repo.add(step)
    session.flush()
    fetched = repo.get(step.id)
    assert fetched is not None
    assert fetched.id == step.id
    assert fetched.run_id == step.run_id


def test_get_returns_none_for_missing_id(session) -> None:
    repo = RunStepRepository(session)
    assert repo.get(RunStepId.new()) is None


def test_save_persists_status_transition(session) -> None:
    run_id = _make_run(session)
    repo = RunStepRepository(session)
    step = RunStep.new(id=RunStepId.new(), run_id=run_id, name="s", position=0, created_at=T0)
    repo.add(step)
    session.flush()
    step.start(T0)
    repo.save(step)
    session.flush()
    fetched = repo.get(step.id)
    assert fetched is not None
    assert fetched.status == step.status


def test_list_for_run_orders_by_position_then_id(session) -> None:
    run_id = _make_run(session)
    repo = RunStepRepository(session)
    step_a = RunStep.new(id=RunStepId.new(), run_id=run_id, name="a", position=0, created_at=T0)
    step_b = RunStep.new(id=RunStepId.new(), run_id=run_id, name="b", position=1, created_at=T0)
    repo.add(step_b)
    repo.add(step_a)
    session.flush()
    result = repo.list_for_run(run_id)
    assert [s.id for s in result] == [step_a.id, step_b.id]

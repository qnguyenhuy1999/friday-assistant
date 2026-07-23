from __future__ import annotations

from datetime import UTC, datetime, timedelta

from friday.domain import Run, RunId, Task, TaskId, ToolInvocation, ToolInvocationId
from friday.infrastructure.persistence.repositories import (
    RunRepository,
    TaskRepository,
    ToolInvocationRepository,
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


def _make_invocation(run_id: RunId, requested_at: datetime) -> ToolInvocation:
    return ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="x",
        requested_input=None,
        requested_at=requested_at,
    )


def test_add_then_get_round_trips(session) -> None:
    run_id = _make_run(session)
    repo = ToolInvocationRepository(session)
    invocation = _make_invocation(run_id, T0)
    repo.add(invocation)
    session.flush()
    fetched = repo.get(invocation.id)
    assert fetched is not None
    assert fetched.id == invocation.id
    assert fetched.run_id == invocation.run_id


def test_get_returns_none_for_missing_id(session) -> None:
    repo = ToolInvocationRepository(session)
    assert repo.get(ToolInvocationId.new()) is None


def test_save_persists_status_transition(session) -> None:
    run_id = _make_run(session)
    repo = ToolInvocationRepository(session)
    invocation = _make_invocation(run_id, T0)
    repo.add(invocation)
    session.flush()
    invocation.start(T0)
    repo.save(invocation)
    session.flush()
    fetched = repo.get(invocation.id)
    assert fetched is not None
    assert fetched.status == invocation.status


def test_save_round_trips_output_set_flag_when_output_is_none(session) -> None:
    run_id = _make_run(session)
    repo = ToolInvocationRepository(session)
    invocation = _make_invocation(run_id, T0)
    repo.add(invocation)
    session.flush()
    invocation.start(T0)
    invocation.succeed(T0, output=None)
    repo.save(invocation)
    session.flush()
    fetched = repo.get(invocation.id)
    assert fetched is not None
    assert fetched.output_set is True
    assert fetched.output is None


def test_list_for_run_orders_by_requested_at_then_id(session) -> None:
    run_id = _make_run(session)
    repo = ToolInvocationRepository(session)
    invocation_b = _make_invocation(run_id, T0 + timedelta(seconds=1))
    invocation_a = _make_invocation(run_id, T0)
    repo.add(invocation_b)
    repo.add(invocation_a)
    session.flush()
    result = repo.list_for_run(run_id)
    assert [i.id for i in result] == [invocation_a.id, invocation_b.id]

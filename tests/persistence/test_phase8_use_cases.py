"""Phase 8 use cases against the real SQLAlchemy Unit of Work.

Fault-injection tests sabotage one coordination boundary at a time and then
read through a NEW session to prove no partial ApprovalRequest / Run /
RunStep / ToolInvocation / Artifact / RunEvent state became durable.

Cross-reference proof: `approval_request_id` columns on runs, run_steps, and
tool_invocations intentionally have no database foreign key (accepted Phase 5
schema decision), so the invalid-reference tests here are the enforcement.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from friday.application.approval_workflow import (
    ApproveRequest,
    ExpireApproval,
    RequestApproval,
)
from friday.application.artifact_use_cases import RecordArtifact
from friday.application.commands import (
    ApproveRequestCommand,
    CreateTaskCommand,
    ExpireApprovalCommand,
    RecordArtifactCommand,
    RequestApprovalCommand,
    RequestToolInvocationCommand,
    StartQueuedRunCommand,
    StartRunCommand,
)
from friday.application.create_task import CreateTask
from friday.application.errors import EntityConflict
from friday.application.lifecycle import StartQueuedRun
from friday.application.start_run import StartRun
from friday.application.tool_invocation_lifecycle import RequestToolInvocation
from friday.domain.approval import ApprovalCategory, ApprovalStatus
from friday.domain.artifact import ArtifactKind
from friday.domain.event import RunEvent
from friday.domain.identifiers import ApprovalRequestId, RunId
from friday.domain.run import Run, RunStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWork,
    create_unit_of_work_factory,
)
from tests.application.fakes import FakeClock

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(hours=1)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _running_run(session_factory: sessionmaker[Session]) -> RunId:
    factory = create_unit_of_work_factory(session_factory)
    task_id = CreateTask(factory, FakeClock(T0)).execute(CreateTaskCommand("t", "d")).task_id
    run_id = StartRun(factory, FakeClock(T0)).execute(StartRunCommand(task_id)).run_id
    StartQueuedRun(factory, FakeClock(T1)).execute(StartQueuedRunCommand(run_id))
    return run_id


def _approval_command(run_id: RunId) -> RequestApprovalCommand:
    return RequestApprovalCommand(
        run_id=run_id,
        category=ApprovalCategory.TOOL_EXECUTION,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input={"path": "/tmp"},
        expires_at=T2,
    )


def _fresh(
    session_factory: sessionmaker[Session], run_id: RunId
) -> tuple[Run | None, list[RunEvent], int]:
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        run = uow.runs.get(run_id)
        events = uow.events.list_for_run(run_id)
        pending = len(uow.approvals.list_pending_for_run(run_id))
        return run, events, pending


def test_full_approval_round_trip_survives_fresh_session(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = _running_run(session_factory)
    factory = create_unit_of_work_factory(session_factory)
    approval = RequestApproval(factory, FakeClock(T1)).execute(_approval_command(run_id))

    run, events, pending = _fresh(session_factory, run_id)
    assert run is not None and run.status is RunStatus.WAITING_FOR_APPROVAL
    assert pending == 1
    assert [e.type.value for e in events[-2:]] == [
        "approval_requested",
        "run_waiting_for_approval",
    ]

    ApproveRequest(factory, FakeClock(T1)).execute(
        ApproveRequestCommand(approval.approval_id, "alice")
    )
    run, events, pending = _fresh(session_factory, run_id)
    assert run is not None and run.status is RunStatus.RUNNING
    assert pending == 0
    assert [e.type.value for e in events[-2:]] == ["approval_resolved", "run_resumed"]
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        stored = uow.approvals.get(approval.approval_id)
        assert stored is not None
        assert stored.status is ApprovalStatus.APPROVED
        assert stored.expires_at == T2


def test_expire_round_trip_uses_persisted_deadline(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = _running_run(session_factory)
    factory = create_unit_of_work_factory(session_factory)
    approval = RequestApproval(factory, FakeClock(T1)).execute(_approval_command(run_id))
    with pytest.raises(EntityConflict):
        ExpireApproval(factory, FakeClock(T1)).execute(ExpireApprovalCommand(approval.approval_id))
    ExpireApproval(factory, FakeClock(T2)).execute(ExpireApprovalCommand(approval.approval_id))
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        stored = uow.approvals.get(approval.approval_id)
        assert stored is not None and stored.status is ApprovalStatus.EXPIRED


def _assert_no_partial_approval_state(
    session_factory: sessionmaker[Session], run_id: RunId, events_before: int
) -> None:
    run, events, pending = _fresh(session_factory, run_id)
    assert run is not None and run.status is RunStatus.RUNNING
    assert pending == 0
    assert len(events) == events_before


@pytest.mark.parametrize(
    "boundary",
    ["approvals_add", "runs_save", "steps_save", "next_sequence", "events_append", "commit"],
)
def test_request_approval_fault_injection_leaves_no_partial_state(
    session_factory: sessionmaker[Session], boundary: str
) -> None:
    run_id = _running_run(session_factory)
    _, events, _ = _fresh(session_factory, run_id)
    events_before = len(events)

    uow = SqlAlchemyUnitOfWork(session_factory())

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"{boundary} failed")

    if boundary == "approvals_add":
        uow.approvals.add = boom  # type: ignore[method-assign]
    elif boundary == "runs_save":
        uow.runs.save = boom  # type: ignore[method-assign]
    elif boundary == "steps_save":
        uow.steps.save = boom  # type: ignore[method-assign]
    elif boundary == "next_sequence":
        uow.events.next_sequence = boom  # type: ignore[method-assign, assignment]
    elif boundary == "events_append":
        uow.events.append = boom  # type: ignore[method-assign]
    else:
        uow.commit = boom  # type: ignore[method-assign]

    command = _approval_command(run_id)
    if boundary == "steps_save":
        # step coordination boundary needs a running step in scope
        from friday.application.commands import CreateOrderedStepCommand, StartStepCommand
        from friday.application.lifecycle import CreateOrderedStep, StartStep

        factory = create_unit_of_work_factory(session_factory)
        step_id = (
            CreateOrderedStep(factory, FakeClock(T1))
            .execute(CreateOrderedStepCommand(run_id, "s"))
            .step_id
        )
        StartStep(factory, FakeClock(T1)).execute(StartStepCommand(step_id))
        _, events, _ = _fresh(session_factory, run_id)
        events_before = len(events)
        command = RequestApprovalCommand(
            run_id=run_id,
            category=ApprovalCategory.TOOL_EXECUTION,
            summary="s",
            reason="r",
            requested_action="a",
            requested_input=None,
            step_id=step_id,
        )

    with pytest.raises(RuntimeError):
        RequestApproval(lambda: uow, FakeClock(T1)).execute(command)
    _assert_no_partial_approval_state(session_factory, run_id, events_before)


def test_approval_resolution_fault_before_entity_resume_rolls_back_everything(
    session_factory: sessionmaker[Session],
) -> None:
    """Sabotage after the ApprovalRequest transition but before the Run
    resume becomes durable: the approval must stay pending on re-read."""
    run_id = _running_run(session_factory)
    factory = create_unit_of_work_factory(session_factory)
    approval = RequestApproval(factory, FakeClock(T1)).execute(_approval_command(run_id))
    _, events, _ = _fresh(session_factory, run_id)
    events_before = len(events)

    uow = SqlAlchemyUnitOfWork(session_factory())

    def failing_run_save(run: Run) -> None:
        raise RuntimeError("run resume persistence failed")

    uow.runs.save = failing_run_save  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        ApproveRequest(lambda: uow, FakeClock(T1)).execute(
            ApproveRequestCommand(approval.approval_id, "alice")
        )

    run, events, pending = _fresh(session_factory, run_id)
    assert run is not None and run.status is RunStatus.WAITING_FOR_APPROVAL
    assert pending == 1  # approval still pending, transition not durable
    assert len(events) == events_before


@pytest.mark.parametrize("boundary", ["tool_add", "events_append", "commit"])
def test_tool_invocation_request_fault_injection_leaves_no_partial_state(
    session_factory: sessionmaker[Session], boundary: str
) -> None:
    run_id = _running_run(session_factory)
    _, events, _ = _fresh(session_factory, run_id)
    events_before = len(events)

    uow = SqlAlchemyUnitOfWork(session_factory())

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"{boundary} failed")

    if boundary == "tool_add":
        uow.tool_invocations.add = boom  # type: ignore[method-assign]
    elif boundary == "events_append":
        uow.events.append = boom  # type: ignore[method-assign]
    else:
        uow.commit = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        RequestToolInvocation(lambda: uow, FakeClock(T1)).execute(
            RequestToolInvocationCommand(run_id, "shell", {"cmd": "ls"})
        )

    with SqlAlchemyUnitOfWork(session_factory()) as fresh:
        assert fresh.tool_invocations.list_for_run(run_id) == []
        assert len(fresh.events.list_for_run(run_id)) == events_before


@pytest.mark.parametrize("boundary", ["artifact_add", "events_append", "commit"])
def test_record_artifact_fault_injection_leaves_no_partial_state(
    session_factory: sessionmaker[Session], boundary: str
) -> None:
    run_id = _running_run(session_factory)
    _, events, _ = _fresh(session_factory, run_id)
    events_before = len(events)

    uow = SqlAlchemyUnitOfWork(session_factory())

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"{boundary} failed")

    if boundary == "artifact_add":
        uow.artifacts.add = boom  # type: ignore[method-assign]
    elif boundary == "events_append":
        uow.events.append = boom  # type: ignore[method-assign]
    else:
        uow.commit = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        RecordArtifact(lambda: uow, FakeClock(T1)).execute(
            RecordArtifactCommand(
                run_id=run_id,
                kind=ArtifactKind.FILE,
                name="out.log",
                media_type="text/plain",
                location="/tmp/out.log",
            )
        )

    with SqlAlchemyUnitOfWork(session_factory()) as fresh:
        assert fresh.artifacts.list_for_run(run_id) == []
        assert len(fresh.events.list_for_run(run_id)) == events_before


def test_unenforced_approval_reference_is_rejected_before_commit(
    session_factory: sessionmaker[Session],
) -> None:
    """`tool_invocations.approval_request_id` has no FK; the application
    validation is the only integrity layer, and a rejected reference must
    leave no invocation row and no event."""
    run_id = _running_run(session_factory)
    factory = create_unit_of_work_factory(session_factory)
    _, events, _ = _fresh(session_factory, run_id)
    events_before = len(events)

    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            RequestToolInvocationCommand(
                run_id, "shell", None, approval_request_id=ApprovalRequestId.new()
            )
        )

    with SqlAlchemyUnitOfWork(session_factory()) as fresh:
        assert fresh.tool_invocations.list_for_run(run_id) == []
        assert len(fresh.events.list_for_run(run_id)) == events_before


def test_cross_run_approval_reference_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    run_a = _running_run(session_factory)
    run_b = _running_run(session_factory)
    factory = create_unit_of_work_factory(session_factory)
    approval_b = RequestApproval(factory, FakeClock(T1)).execute(_approval_command(run_b))
    ApproveRequest(factory, FakeClock(T1)).execute(
        ApproveRequestCommand(approval_b.approval_id, "alice")
    )

    with pytest.raises(EntityConflict):
        RequestToolInvocation(factory, FakeClock(T1)).execute(
            RequestToolInvocationCommand(
                run_a, "shell", None, approval_request_id=approval_b.approval_id
            )
        )
    with SqlAlchemyUnitOfWork(session_factory()) as fresh:
        assert fresh.tool_invocations.list_for_run(run_a) == []

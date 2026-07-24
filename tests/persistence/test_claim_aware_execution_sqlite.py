"""Claim-aware tool execution against real SQLite with independent Units of
Work: fresh sessions observe exactly what each committed transaction wrote,
and a claim stolen between Txn A and Txn B leaves the invocation RUNNING
with its approval consumed — the documented at-least-once ambiguity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.errors import ClaimLost
from friday.application.ports import UnitOfWorkFactory
from friday.application.tool_authorization import compute_authorization_fingerprint
from friday.application.tool_gateway import (
    ToolCall,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.event import RunEventType
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

T0 = datetime(2026, 1, 1, tzinfo=UTC)
LEASE = timedelta(minutes=1)
WRITE_CALL = ToolCall(tool="workspace.write_text", tool_input={"path": "b.txt", "content": "x"})


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class ScriptedGateway:
    """Returns a fixed success; optionally runs a hook (in its own UoW)
    while the tool 'executes' — i.e. between Txn A and Txn B."""

    def __init__(self, on_execute: object = None) -> None:
        self.on_execute = on_execute

    def list_tools(self) -> tuple[()]:
        return ()

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        return ToolRiskAssessment(
            tool=call.tool,
            read_only=False,
            approval_required=True,
            category=ApprovalCategory.FILESYSTEM_WRITE,
            summary=call.tool,
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        if callable(self.on_execute):
            self.on_execute()
        return ToolExecutionResult.succeeded({"path": "b.txt"})


@pytest.fixture
def uow_factory(tmp_path: Path) -> UnitOfWorkFactory:
    engine = create_engine(f"sqlite:///{tmp_path / 'exec.db'}")
    Base.metadata.create_all(engine)
    return create_unit_of_work_factory(create_session_factory(engine))


def _seed_claimed_run(uow_factory: UnitOfWorkFactory) -> tuple[RunId, int]:
    # one aggregate per transaction — ORM flush order across unrelated
    # mappers is not FK-aware in this schema
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    with uow_factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    run.start(T0)
    with uow_factory() as uow:
        uow.runs.add(run)
        uow.commit()
    with uow_factory() as uow:
        uow.work_queue.enqueue(run.id, available_at=T0, enqueued_at=T0)
        assert uow.work_queue.try_claim(run.id, "w1", "tok", T0, T0 + LEASE)
        item = uow.work_queue.get(run.id)
        assert item is not None
        generation = item.claim_generation
        approval = ApprovalRequest.new(
            id=ApprovalRequestId.new(),
            run_id=run.id,
            category=ApprovalCategory.FILESYSTEM_WRITE,
            summary="write b.txt",
            reason="",
            requested_action=WRITE_CALL.tool,
            requested_input=WRITE_CALL.tool_input,
            requested_at=T0,
            authorization_fingerprint=compute_authorization_fingerprint(
                run_id=run.id, step_id=None, call=WRITE_CALL
            ),
        )
        approval.approve(T0, resolver="patrick")
        uow.approvals.add(approval)
        uow.commit()
        return run.id, generation


def test_happy_path_is_visible_to_a_fresh_session(uow_factory: UnitOfWorkFactory) -> None:
    run_id, generation = _seed_claimed_run(uow_factory)
    executor = ExecuteToolAction(
        uow_factory, FixedClock(T0 + timedelta(seconds=1)), ScriptedGateway()
    )
    outcome = executor.execute(
        run_id=run_id,
        step_id=None,
        call=WRITE_CALL,
        worker_id="w1",
        claim_token="tok",
        claim_generation=generation,
    )
    assert outcome.kind == "executed"
    with uow_factory() as fresh:
        invocations = fresh.tool_invocations.list_for_run(run_id)
        assert len(invocations) == 1
        assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
        assert invocations[0].output == {"path": "b.txt"}
        approvals = fresh.approvals.list_for_run(run_id)
        assert approvals[0].is_consumed is True
        event_types = [event.type for event in fresh.events.list_for_run(run_id)]
        assert RunEventType.TOOL_INVOCATION_SUCCEEDED in event_types


def test_claim_stolen_mid_execution_blocks_result_persistence(
    uow_factory: UnitOfWorkFactory,
) -> None:
    run_id, generation = _seed_claimed_run(uow_factory)

    def steal_claim() -> None:
        # an independent transaction (another worker/maintenance) removes
        # the work item while the tool is executing
        with uow_factory() as other:
            other.work_queue.remove(run_id)
            other.commit()

    executor = ExecuteToolAction(
        uow_factory, FixedClock(T0 + timedelta(seconds=1)), ScriptedGateway(steal_claim)
    )
    with pytest.raises(ClaimLost):
        executor.execute(
            run_id=run_id,
            step_id=None,
            call=WRITE_CALL,
            worker_id="w1",
            claim_token="tok",
            claim_generation=generation,
        )
    with uow_factory() as fresh:
        invocations = fresh.tool_invocations.list_for_run(run_id)
        assert len(invocations) == 1
        # Txn A committed (invocation exists, approval consumed) but the
        # result was never persisted: the documented ambiguous state
        assert invocations[0].status is ToolInvocationStatus.RUNNING
        assert invocations[0].output_set is False
        approvals = fresh.approvals.list_for_run(run_id)
        assert approvals[0].is_consumed is True
        event_types = [event.type for event in fresh.events.list_for_run(run_id)]
        assert RunEventType.TOOL_INVOCATION_SUCCEEDED not in event_types
        assert RunEventType.TOOL_INVOCATION_STARTED in event_types

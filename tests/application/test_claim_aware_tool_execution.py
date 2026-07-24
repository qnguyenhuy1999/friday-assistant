"""Claim-aware tool execution: transaction discipline (two short txns, none
open during execution), approval interception, consumption, replay policy,
claim-loss fencing at every checkpoint, and artifact recording."""

from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.errors import ClaimLost, ToolExecutionAmbiguous, ToolNotFound
from friday.application.tool_authorization import compute_authorization_fingerprint
from friday.application.tool_gateway import (
    ArtifactCandidate,
    ToolCall,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.artifact import ArtifactKind
from friday.domain.event import RunEventType
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

LEASE = timedelta(seconds=60)
READ_CALL = ToolCall(tool="workspace.read_text", tool_input={"path": "a.txt"})
WRITE_CALL = ToolCall(tool="workspace.write_text", tool_input={"path": "b.txt", "content": "x"})
TOOL_FAILURE = Failure(
    code="tool_timeout", message="too slow", retryable=True, cause=FailureCause.TIMEOUT
)


class FakeGateway:
    """Scripted ToolGateway: records execute() requests, returns a queued
    result, and knows which tools require approval."""

    def __init__(self, result: ToolExecutionResult | None = None) -> None:
        self.result = result or ToolExecutionResult.succeeded({"ok": True})
        self.executed: list[ToolExecutionRequest] = []
        self.uow_open_during_execute: list[bool] = []
        self.watched_uow: FakeUnitOfWork | None = None

    def list_tools(self) -> tuple[()]:
        return ()

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        if call.tool == "browser.click":
            raise ToolNotFound(call.tool)
        mutating = call.tool == "workspace.write_text" or call.tool == "process.run"
        return ToolRiskAssessment(
            tool=call.tool,
            read_only=not mutating,
            approval_required=mutating,
            category=ApprovalCategory.FILESYSTEM_WRITE
            if call.tool == "workspace.write_text"
            else ApprovalCategory.TOOL_EXECUTION,
            summary=f"{call.tool}",
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.executed.append(request)
        if self.watched_uow is not None:
            self.uow_open_during_execute.append(not self.watched_uow.closed)
        return self.result


def _claimed_run() -> tuple[FakeUnitOfWork, CountingUnitOfWorkFactory, Run, int]:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    uow.task_repo.add(task)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    run.start(T0)
    uow.run_repo.add(run)
    uow.work_queue_repo.enqueue(run.id, available_at=T0, enqueued_at=T0)
    assert uow.work_queue_repo.try_claim(run.id, "w1", "tok", T0, T0 + LEASE)
    item = uow.work_queue_repo.get(run.id)
    assert item is not None
    return uow, factory, run, item.claim_generation


def _executor(factory: CountingUnitOfWorkFactory, gateway: FakeGateway) -> ExecuteToolAction:
    return ExecuteToolAction(factory, FakeClock(T0 + timedelta(seconds=1)), gateway)


def _run_call(
    executor: ExecuteToolAction, run: Run, generation: int, call: ToolCall = READ_CALL
) -> object:
    return executor.execute(
        run_id=run.id,
        step_id=None,
        call=call,
        worker_id="w1",
        claim_token="tok",
        claim_generation=generation,
    )


def _approved_for(run: Run, call: ToolCall, *, consumed: bool = False) -> ApprovalRequest:
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run.id,
        category=ApprovalCategory.FILESYSTEM_WRITE,
        summary="s",
        reason="",
        requested_action=call.tool,
        requested_input=call.tool_input,
        requested_at=T0,
        authorization_fingerprint=compute_authorization_fingerprint(
            run_id=run.id, step_id=None, call=call
        ),
    )
    approval.approve(T0, resolver="patrick")
    if consumed:
        approval.consume(T0)
    return approval


# --- read-only path --------------------------------------------------------


def test_read_only_tool_executes_without_approval() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    outcome = _run_call(_executor(factory, gateway), run, generation)
    assert outcome.kind == "executed"  # type: ignore[attr-defined]
    assert len(gateway.executed) == 1
    invocations = uow.tool_repo.list_for_run(run.id)
    assert len(invocations) == 1
    assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
    assert invocations[0].output == {"ok": True}
    assert invocations[0].approval_request_id is None
    event_types = [event.type for event in uow.event_store.list_for_run(run.id)]
    assert event_types == [
        RunEventType.TOOL_INVOCATION_REQUESTED,
        RunEventType.TOOL_INVOCATION_STARTED,
        RunEventType.TOOL_INVOCATION_SUCCEEDED,
    ]
    assert uow.commit_count == 2  # Txn A + Txn B


def test_no_transaction_is_open_while_the_tool_runs() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    gateway.watched_uow = uow
    _run_call(_executor(factory, gateway), run, generation)
    assert gateway.uow_open_during_execute == [False]


def test_gateway_receives_the_invocation_identity() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    _run_call(_executor(factory, gateway), run, generation)
    request = gateway.executed[0]
    assert request.run_id == run.id
    assert request.call == READ_CALL
    assert uow.tool_repo.get(request.invocation_id) is not None


def test_tool_failure_is_persisted_as_structured_failure() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway(result=ToolExecutionResult.failed(TOOL_FAILURE))
    outcome = _run_call(_executor(factory, gateway), run, generation)
    assert outcome.result.status == "failed"  # type: ignore[attr-defined]
    invocation = uow.tool_repo.list_for_run(run.id)[0]
    assert invocation.status is ToolInvocationStatus.FAILED
    assert invocation.failure == TOOL_FAILURE
    event_types = [event.type for event in uow.event_store.list_for_run(run.id)]
    assert RunEventType.TOOL_INVOCATION_FAILED in event_types


def test_artifacts_are_recorded_with_events() -> None:
    uow, factory, run, generation = _claimed_run()
    candidate = ArtifactCandidate(
        kind=ArtifactKind.FILE,
        name="b.txt",
        media_type="text/plain",
        location="b.txt",
        size=1,
        checksum="c" * 64,
    )
    gateway = FakeGateway(
        result=ToolExecutionResult.succeeded({"path": "b.txt"}, artifacts=(candidate,))
    )
    uow.approval_repo.add(_approved_for(run, WRITE_CALL))
    _run_call(_executor(factory, gateway), run, generation, call=WRITE_CALL)
    artifacts = uow.artifact_repo.list_for_run(run.id)
    assert len(artifacts) == 1
    assert artifacts[0].location == "b.txt"
    assert artifacts[0].checksum == "c" * 64
    event_types = [event.type for event in uow.event_store.list_for_run(run.id)]
    assert RunEventType.ARTIFACT_CREATED in event_types


# --- approval interception --------------------------------------------------


def test_protected_tool_without_approval_is_intercepted() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    outcome = _run_call(_executor(factory, gateway), run, generation, call=WRITE_CALL)
    assert outcome.kind == "approval_required"  # type: ignore[attr-defined]
    assert gateway.executed == []  # nothing ran
    assert uow.tool_repo.list_for_run(run.id) == []  # no invocation row


def test_approved_exact_action_executes_and_consumes() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    approval = _approved_for(run, WRITE_CALL)
    uow.approval_repo.add(approval)
    outcome = _run_call(_executor(factory, gateway), run, generation, call=WRITE_CALL)
    assert outcome.kind == "executed"  # type: ignore[attr-defined]
    assert approval.is_consumed is True
    invocation = uow.tool_repo.list_for_run(run.id)[0]
    assert invocation.approval_request_id == approval.id


def test_approval_for_different_input_does_not_authorize() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    other_call = ToolCall(
        tool="workspace.write_text", tool_input={"path": "OTHER.txt", "content": "x"}
    )
    uow.approval_repo.add(_approved_for(run, other_call))
    outcome = _run_call(_executor(factory, gateway), run, generation, call=WRITE_CALL)
    assert outcome.kind == "approval_required"  # type: ignore[attr-defined]
    assert gateway.executed == []


def test_rejected_approval_never_executes() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    approval = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run.id,
        category=ApprovalCategory.FILESYSTEM_WRITE,
        summary="s",
        reason="",
        requested_action=WRITE_CALL.tool,
        requested_input=WRITE_CALL.tool_input,
        requested_at=T0,
        authorization_fingerprint=compute_authorization_fingerprint(
            run_id=run.id, step_id=None, call=WRITE_CALL
        ),
    )
    approval.reject(T0, resolver="patrick")
    uow.approval_repo.add(approval)
    outcome = _run_call(_executor(factory, gateway), run, generation, call=WRITE_CALL)
    assert outcome.kind == "approval_required"  # type: ignore[attr-defined]
    assert gateway.executed == []
    assert approval.status is ApprovalStatus.REJECTED  # untouched


# --- replay policy ----------------------------------------------------------


def test_replayed_succeeded_protected_action_reuses_result() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    approval = _approved_for(run, WRITE_CALL, consumed=True)
    uow.approval_repo.add(approval)
    executor = _executor(factory, gateway)
    # a prior invocation bound to the consumed approval, already succeeded
    from friday.domain.identifiers import ToolInvocationId
    from friday.domain.tool import ToolInvocation

    prior = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        tool_name=WRITE_CALL.tool,
        requested_input=WRITE_CALL.tool_input,
        requested_at=T0,
        approval_request_id=approval.id,
    )
    prior.start(T0)
    prior.succeed(T0, {"path": "b.txt"})
    uow.tool_repo.add(prior)

    outcome = _run_call(executor, run, generation, call=WRITE_CALL)
    assert outcome.kind == "executed"  # type: ignore[attr-defined]
    assert outcome.replayed is True  # type: ignore[attr-defined]
    assert outcome.invocation_id == prior.id  # type: ignore[attr-defined]
    assert gateway.executed == []  # never re-executed
    assert len(uow.tool_repo.list_for_run(run.id)) == 1  # no duplicate row


def test_replayed_running_protected_action_is_ambiguous() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    approval = _approved_for(run, WRITE_CALL, consumed=True)
    uow.approval_repo.add(approval)
    from friday.domain.identifiers import ToolInvocationId
    from friday.domain.tool import ToolInvocation

    prior = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        tool_name=WRITE_CALL.tool,
        requested_input=WRITE_CALL.tool_input,
        requested_at=T0,
        approval_request_id=approval.id,
    )
    prior.start(T0)  # still RUNNING — side effect may have completed
    uow.tool_repo.add(prior)

    with pytest.raises(ToolExecutionAmbiguous):
        _run_call(_executor(factory, gateway), run, generation, call=WRITE_CALL)
    assert gateway.executed == []


# --- claim fencing ----------------------------------------------------------


def test_claim_lost_before_txn_a_persists_nothing() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    executor = _executor(factory, gateway)
    with pytest.raises(ClaimLost):
        executor.execute(
            run_id=run.id,
            step_id=None,
            call=READ_CALL,
            worker_id="w2",
            claim_token="wrong",
            claim_generation=generation,
        )
    assert gateway.executed == []
    assert uow.tool_repo.list_for_run(run.id) == []
    assert uow.commit_count == 0


def test_claim_lost_after_execution_leaves_invocation_running() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()

    original = uow.work_queue_repo.is_claim_active
    calls = {"n": 0}

    def lose_after_first(*args: object, **kwargs: object) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return original(*args, **kwargs)  # type: ignore[arg-type]
        return False  # claim gone by Txn B

    uow.work_queue_repo.is_claim_active = lose_after_first  # type: ignore[method-assign]
    with pytest.raises(ClaimLost):
        _run_call(_executor(factory, gateway), run, generation)
    # the tool DID run — but its result was never persisted
    assert len(gateway.executed) == 1
    invocation = uow.tool_repo.list_for_run(run.id)[0]
    assert invocation.status is ToolInvocationStatus.RUNNING
    assert uow.commit_count == 1  # only Txn A


def test_unknown_tool_raises_before_any_persistence() -> None:
    uow, factory, run, generation = _claimed_run()
    gateway = FakeGateway()
    with pytest.raises(ToolNotFound):
        _run_call(
            _executor(factory, gateway),
            run,
            generation,
            call=ToolCall(tool="browser.click", tool_input={}),
        )
    assert uow.commit_count == 0
    assert factory.calls == 0  # no transaction ever opened

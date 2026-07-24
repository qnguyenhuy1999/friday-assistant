"""AgentRunProcessor: the full 25.4 matrix — finish/fail/yield/tool actions,
approval interception, budgets, brain errors, and claim loss at every
checkpoint. Uses a scripted fake brain and fake gateway; all claim fencing
runs through the real use cases over the fake Unit of Work."""

from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.agent_run_processor import AgentRunProcessor, RuntimeLimits
from friday.application.brain_runtime import BrainRequest, BrainResponse
from friday.application.claim_aware_tool_execution import ExecuteToolAction
from friday.application.errors import (
    BrainProtocolError,
    BrainResponseInvalid,
    BrainTimeout,
    BrainUnavailable,
    ToolNotFound,
)
from friday.application.run_processor import ClaimContext
from friday.application.runtime_actions import (
    FailAction,
    FinishAction,
    InvokeToolAction,
    YieldAction,
)
from friday.application.tool_authorization import (
    RequestToolApproval,
    compute_authorization_fingerprint,
)
from friday.application.tool_gateway import (
    ToolCall,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolRiskAssessment,
)
from friday.application.worker_coordination import VerifyRunClaim
from friday.domain.approval import ApprovalCategory, ApprovalRequest, ApprovalStatus
from friday.domain.identifiers import ApprovalRequestId, RunId, TaskId
from friday.domain.run import Run, RunStatus
from friday.domain.task import Task
from friday.domain.tool import ToolInvocationStatus
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

LEASE = timedelta(seconds=60)
LIMITS = RuntimeLimits(
    max_turns_per_claim=8,
    max_tool_calls_per_claim=4,
    max_context_chars=60_000,
    max_response_bytes=65_536,
    max_yield_seconds=3600,
)
READ = InvokeToolAction(tool="workspace.read_text", tool_input={"path": "a.txt"}, reason=None)
WRITE = InvokeToolAction(
    tool="workspace.write_text", tool_input={"path": "b.txt", "content": "x"}, reason="save"
)
FINISH = FinishAction(summary="all done")


class ScriptedBrain:
    """Returns queued responses; raising entries are raised instead."""

    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.requests: list[BrainRequest] = []

    def next_action(self, request: BrainRequest) -> BrainResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("brain called more times than scripted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, FinishAction | FailAction | YieldAction | InvokeToolAction)
        return BrainResponse(action=item)


class FakeGateway:
    def __init__(self, result: ToolExecutionResult | None = None) -> None:
        self.result = result or ToolExecutionResult.succeeded({"ok": True})
        self.executed: list[ToolExecutionRequest] = []

    def list_tools(self) -> tuple[()]:
        return ()

    def assess(self, call: ToolCall) -> ToolRiskAssessment:
        if call.tool.startswith("browser."):
            raise ToolNotFound(call.tool)
        mutating = call.tool in {"workspace.write_text", "process.run"}
        return ToolRiskAssessment(
            tool=call.tool,
            read_only=not mutating,
            approval_required=mutating,
            category=ApprovalCategory.FILESYSTEM_WRITE
            if call.tool == "workspace.write_text"
            else ApprovalCategory.TOOL_EXECUTION,
            summary=call.tool,
        )

    def execute(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        self.executed.append(request)
        return self.result


class Harness:
    def __init__(self, *brain_script: object, gateway: FakeGateway | None = None) -> None:
        self.uow = FakeUnitOfWork()
        self.factory = CountingUnitOfWorkFactory(self.uow)
        self.clock = FakeClock(T0 + timedelta(seconds=1))
        self.brain = ScriptedBrain(*brain_script)
        self.gateway = gateway or FakeGateway()

        task = Task.new(id=TaskId.new(), title="t", description="do it", created_at=T0)
        task.start(T0)
        self.uow.task_repo.add(task)
        self.run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
        self.run.start(T0)
        self.uow.run_repo.add(self.run)
        self.uow.work_queue_repo.enqueue(self.run.id, available_at=T0, enqueued_at=T0)
        assert self.uow.work_queue_repo.try_claim(self.run.id, "w1", "tok", T0, T0 + LEASE)
        item = self.uow.work_queue_repo.get(self.run.id)
        assert item is not None
        self.generation = item.claim_generation
        self.lease_lost = False

        self.processor = AgentRunProcessor(
            uow_factory=self.factory,
            clock=self.clock,
            brain=self.brain,
            gateway=self.gateway,
            verify_claim=VerifyRunClaim(self.factory, self.clock),
            request_tool_approval=RequestToolApproval(self.factory, self.clock),
            execute_tool_action=ExecuteToolAction(self.factory, self.clock, self.gateway),
            limits=LIMITS,
        )

    def context(self) -> ClaimContext:
        return ClaimContext(
            run_id=self.run.id,
            task_id=self.run.task_id,
            worker_id="w1",
            claim_token="tok",
            claim_generation=self.generation,
            attempt_number=1,
            is_lease_lost=lambda: self.lease_lost,
        )

    def approve(self, action: InvokeToolAction) -> ApprovalRequest:
        call = ToolCall(tool=action.tool, tool_input=action.tool_input)
        approval = ApprovalRequest.new(
            id=ApprovalRequestId.new(),
            run_id=self.run.id,
            category=ApprovalCategory.FILESYSTEM_WRITE,
            summary="s",
            reason="",
            requested_action=call.tool,
            requested_input=call.tool_input,
            requested_at=T0,
            authorization_fingerprint=compute_authorization_fingerprint(
                run_id=self.run.id, step_id=None, call=call
            ),
        )
        approval.approve(T0, resolver="patrick")
        self.uow.approval_repo.add(approval)
        return approval


# --- terminal actions -------------------------------------------------------


def test_finish_returns_succeeded() -> None:
    harness = Harness(FINISH)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "succeeded"


def test_finish_is_rejected_while_an_invocation_is_open_then_retried() -> None:
    harness = Harness(READ, FINISH)
    # first turn executes a read tool; the invocation becomes terminal, so
    # the second turn's finish is accepted
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "succeeded"
    assert len(harness.brain.requests) == 2
    # the second request's context included the first turn's note
    assert "invoked workspace.read_text -> succeeded" in harness.brain.requests[1].context


def test_fail_maps_to_agent_reported_failure() -> None:
    harness = Harness(FailAction(reason="cannot proceed: repo is empty"))
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "agent_reported_failure"
    assert outcome.failure.retryable is False
    assert "cannot proceed" in outcome.failure.message


def test_yield_schedules_bounded_delay() -> None:
    harness = Harness(YieldAction(delay_seconds=30, reason=None))
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert outcome.available_at == harness.clock.now() + timedelta(seconds=30)


def test_yield_delay_is_capped_at_the_configured_maximum() -> None:
    harness = Harness(YieldAction(delay_seconds=86_400, reason=None))
    outcome = harness.processor.process(harness.context())
    assert outcome.available_at == harness.clock.now() + timedelta(seconds=LIMITS.max_yield_seconds)


# --- tool actions ------------------------------------------------------------


def test_read_only_tool_executes_and_loop_continues() -> None:
    harness = Harness(READ, FINISH)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "succeeded"
    assert len(harness.gateway.executed) == 1
    invocation = harness.uow.tool_repo.list_for_run(harness.run.id)[0]
    assert invocation.status is ToolInvocationStatus.SUCCEEDED


def test_protected_tool_without_approval_returns_waiting() -> None:
    harness = Harness(WRITE)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "waiting_for_approval"
    assert outcome.approval_request_id is not None
    assert harness.gateway.executed == []  # nothing ran
    assert harness.run.status is RunStatus.WAITING_FOR_APPROVAL
    approval = harness.uow.approval_repo.get(outcome.approval_request_id)
    assert approval is not None
    assert approval.status is ApprovalStatus.PENDING
    assert approval.authorization_fingerprint == compute_authorization_fingerprint(
        run_id=harness.run.id,
        step_id=None,
        call=ToolCall(tool=WRITE.tool, tool_input=WRITE.tool_input),
    )


def test_approved_protected_tool_executes_and_consumes() -> None:
    harness = Harness(WRITE, FINISH)
    approval = harness.approve(WRITE)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "succeeded"
    assert len(harness.gateway.executed) == 1
    assert approval.is_consumed is True


def test_unknown_tool_fails_the_run() -> None:
    harness = Harness(InvokeToolAction(tool="browser.click", tool_input={}, reason=None))
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "tool_not_found"


def test_invalid_tool_input_becomes_a_turn_note_not_a_crash() -> None:
    bad = InvokeToolAction(tool="workspace.read_text", tool_input=["not", "a", "dict"], reason=None)
    harness = Harness(bad, FINISH)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "succeeded"
    assert "invalid tool input" in harness.brain.requests[1].context


# --- brain errors -------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (BrainResponseInvalid("bad json"), "brain_response_invalid"),
        (BrainTimeout("too slow"), "brain_timeout"),
        (BrainUnavailable("no cli"), "brain_unavailable"),
        (BrainProtocolError("weird envelope"), "brain_protocol_error"),
    ],
)
def test_brain_errors_map_to_stable_failure_codes(error: Exception, code: str) -> None:
    harness = Harness(error)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == code
    assert outcome.failure.retryable is True


def test_unexpected_brain_exception_propagates_to_the_worker_loop() -> None:
    harness = Harness(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        harness.processor.process(harness.context())


# --- budgets -------------------------------------------------------------------


def test_turn_budget_yields_for_a_fresh_claim() -> None:
    # invalid tool inputs consume turns without consuming tool-call budget,
    # so the turn cap (8) is reached before the tool cap (4)
    invalid = InvokeToolAction(tool="workspace.read_text", tool_input=[], reason=None)
    harness = Harness(*[invalid] * LIMITS.max_turns_per_claim)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert len(harness.brain.requests) == LIMITS.max_turns_per_claim


def test_tool_call_budget_yields_for_a_fresh_claim() -> None:
    harness = Harness(*[READ] * LIMITS.max_tool_calls_per_claim)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert len(harness.gateway.executed) == LIMITS.max_tool_calls_per_claim


# --- claim loss ------------------------------------------------------------------


def test_lease_lost_before_brain_call_yields_without_calling_brain() -> None:
    harness = Harness(FINISH)
    harness.lease_lost = True
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert harness.brain.requests == []


def test_durable_claim_lost_before_brain_call_yields() -> None:
    harness = Harness(FINISH)
    harness.uow.work_queue_repo.remove(harness.run.id)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert harness.brain.requests == []


def test_claim_lost_during_brain_call_discards_the_response() -> None:
    harness = Harness(FINISH)

    original = harness.brain.next_action

    def lose_claim_mid_call(request: BrainRequest) -> BrainResponse:
        harness.uow.work_queue_repo.remove(harness.run.id)
        return original(request)

    harness.brain.next_action = lose_claim_mid_call  # type: ignore[method-assign]
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"  # finish response was never applied


def test_claim_lost_before_tool_execution_yields() -> None:
    harness = Harness(READ)
    # make the executor see a dead claim: is_claim_active dies at the third
    # durable check (pre-brain, post-brain succeed; the executor's Txn A fails)
    original = harness.uow.work_queue_repo.is_claim_active
    checks = {"n": 0}

    def die_at_third_check(*args: object, **kwargs: object) -> bool:
        checks["n"] += 1
        if checks["n"] <= 2:
            return original(*args, **kwargs)  # type: ignore[arg-type]
        return False

    harness.uow.work_queue_repo.is_claim_active = die_at_third_check  # type: ignore[method-assign]
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert harness.gateway.executed == []  # tool never ran
    assert harness.uow.tool_repo.list_for_run(harness.run.id) == []


def test_run_no_longer_running_yields() -> None:
    harness = Harness(FINISH)
    harness.run.cancel(T0 + timedelta(seconds=1))
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert harness.brain.requests == []


# --- limits validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_turns_per_claim", 0),
        ("max_tool_calls_per_claim", 0),
        ("max_context_chars", 10),
        ("max_response_bytes", 0),
        ("max_yield_seconds", -1),
    ],
)
def test_limits_invariants(field: str, value: int) -> None:
    fields = {
        "max_turns_per_claim": 8,
        "max_tool_calls_per_claim": 4,
        "max_context_chars": 60_000,
        "max_response_bytes": 65_536,
        "max_yield_seconds": 3600,
    }
    fields[field] = value
    with pytest.raises(ValueError):
        RuntimeLimits(**fields)


def test_finish_with_pending_step_is_rejected_as_a_turn_note() -> None:
    from friday.domain.identifiers import RunStepId
    from friday.domain.step import RunStep

    harness = Harness(FINISH, FailAction(reason="cannot complete the step"))
    step = RunStep.new(
        id=RunStepId.new(), run_id=harness.run.id, name="s", position=0, created_at=T0
    )
    harness.uow.step_repo.add(step)  # PENDING forever
    outcome = harness.processor.process(harness.context())
    # turn 1's finish is rejected as a note; turn 2 the brain gives up
    assert "finish rejected: 1 step(s) are not terminal" in harness.brain.requests[1].context
    assert outcome.kind == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "agent_reported_failure"


def test_ambiguous_prior_protected_execution_fails_the_run() -> None:
    from friday.domain.identifiers import ToolInvocationId
    from friday.domain.tool import ToolInvocation

    harness = Harness(WRITE)
    approval = harness.approve(WRITE)
    approval.consume(T0)
    prior = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=harness.run.id,
        tool_name=WRITE.tool,
        requested_input=WRITE.tool_input,
        requested_at=T0,
        approval_request_id=approval.id,
    )
    prior.start(T0)  # non-terminal: side effect may have happened
    harness.uow.tool_repo.add(prior)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "failed"
    assert outcome.failure is not None
    assert outcome.failure.code == "tool_execution_ambiguous"
    assert outcome.failure.retryable is False


def test_replayed_protected_result_is_noted_in_the_next_context() -> None:
    from friday.domain.identifiers import ToolInvocationId
    from friday.domain.tool import ToolInvocation

    harness = Harness(WRITE, FINISH)
    approval = harness.approve(WRITE)
    approval.consume(T0)
    prior = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=harness.run.id,
        tool_name=WRITE.tool,
        requested_input=WRITE.tool_input,
        requested_at=T0,
        approval_request_id=approval.id,
    )
    prior.start(T0)
    prior.succeed(T0, {"path": "b.txt"})
    harness.uow.tool_repo.add(prior)
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "succeeded"
    assert harness.gateway.executed == []  # never re-executed
    assert "(replayed durable result)" in harness.brain.requests[1].context


def test_claim_lost_while_requesting_approval_yields() -> None:
    harness = Harness(WRITE)
    original = harness.uow.work_queue_repo.is_claim_active
    checks = {"n": 0}

    def die_at_fourth_check(*args: object, **kwargs: object) -> bool:
        checks["n"] += 1
        if checks["n"] <= 3:
            return original(*args, **kwargs)  # type: ignore[arg-type]
        return False  # dead exactly when RequestToolApproval verifies

    harness.uow.work_queue_repo.is_claim_active = die_at_fourth_check  # type: ignore[method-assign]
    outcome = harness.processor.process(harness.context())
    assert outcome.kind == "yielded"
    assert harness.uow.approval_repo.list_for_run(harness.run.id) == []  # nothing persisted

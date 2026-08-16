from __future__ import annotations

from datetime import timedelta

import pytest

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
    DisableAgent,
    ResolveRunAgent,
)
from friday.application.approval_workflow import ApproveRequest, RequestApproval
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.commands import (
    ApproveRequestCommand,
    CreateTaskCommand,
    RequestApprovalCommand,
)
from friday.application.create_task import CreateTask
from friday.application.delegation import (
    CreateDelegationRequest,
    DispatchDelegation,
    GetDelegationRequest,
    ListDelegationsForRun,
)
from friday.application.errors import (
    AgentNotFound,
    ClaimLost,
    DelegationRequestNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.domain.agent import AgentRevisionSourceKind
from friday.domain.approval import ApprovalCategory, ApprovalSubjectKind
from friday.domain.delegation import DelegationRequest
from friday.domain.event import RunEventType
from friday.domain.identifiers import AgentId, DelegationRequestId, RunId, RunStepId, TaskId
from friday.domain.run import Run
from friday.domain.step import RunStep
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _setup(uow: FakeUnitOfWork, clock: FakeClock, factory: CountingUnitOfWorkFactory):  # type: ignore[no-untyped-def]
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    revision = CreateAgentRevision(factory, clock, _registry()).execute(
        agent_id=agent.id,
        instructions="be helpful",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)
    return agent, run


def test_create_delegation_request_persists_and_validates_parent() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, run = _setup(uow, clock, factory)

    request = CreateDelegationRequest(factory, clock).execute(
        parent_run_id=run.id,
        target_agent_id=agent.id,
        objective="summarize logs",
        input_payload={"a": 1},
        expected_output_contract="json summary",
    )
    assert request.parent_run_id == run.id
    assert request.target_agent_id == agent.id
    fetched = GetDelegationRequest(factory).execute(request.id)
    assert fetched == request
    assert ListDelegationsForRun(factory).execute(run.id) == [request]


def test_creating_delegation_request_has_no_child_or_execution_side_effects() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, run = _setup(uow, clock, factory)
    before = (
        len(uow.task_repo.items),
        len(uow.run_repo.items),
        len(uow.approval_repo.items),
        len(uow.tool_repo.items),
    )

    request = CreateDelegationRequest(factory, clock).execute(
        parent_run_id=run.id,
        target_agent_id=agent.id,
        objective="summarize logs",
        input_payload=None,
        expected_output_contract="a summary",
    )

    assert request.child_task_id is None
    assert request.child_run_id is None
    assert (
        len(uow.task_repo.items),
        len(uow.run_repo.items),
        len(uow.approval_repo.items),
        len(uow.tool_repo.items),
    ) == before


def test_approved_parent_run_approval_has_no_delegation_or_child_authority() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, run = _setup(uow, clock, factory)
    run.start(clock.now())
    approval = RequestApproval(factory, clock).execute(
        RequestApprovalCommand(
            run_id=run.id,
            category=ApprovalCategory.OTHER,
            summary="approve parent delegation proposal",
            reason="test only",
            requested_action="delegate",
            requested_input={"target_agent_id": str(agent.id)},
        )
    )
    approved = ApproveRequest(factory, clock).execute(
        ApproveRequestCommand(approval_id=approval.approval_id, resolver="operator")
    )
    request = CreateDelegationRequest(factory, clock).execute(
        parent_run_id=run.id,
        target_agent_id=agent.id,
        objective="summarize logs",
        input_payload=None,
        expected_output_contract="a summary",
    )

    assert approved.subject_kind is ApprovalSubjectKind.RUN
    assert approved.subject_id == str(run.id)
    assert request.status.value == "requested"
    assert request.child_task_id is None
    assert request.child_run_id is None
    assert request.authorization_fingerprint != approved.authorization_fingerprint
    assert len(uow.task_repo.items) == 1
    assert len(uow.run_repo.items) == 1
    assert len(uow.tool_repo.items) == 0


def test_create_delegation_request_requires_existing_parent_run() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(RunNotFound):
        CreateDelegationRequest(factory, clock).execute(
            parent_run_id=RunId.new(),
            target_agent_id=agent.id,
            objective="x",
            input_payload=None,
            expected_output_contract="y",
        )


def test_create_delegation_request_requires_existing_target_agent() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)
    with pytest.raises(AgentNotFound):
        CreateDelegationRequest(factory, clock).execute(
            parent_run_id=run.id,
            target_agent_id=AgentId.new(),
            objective="x",
            input_payload=None,
            expected_output_contract="y",
        )


def test_create_delegation_request_requires_active_agent_with_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(EntityConflict):
        CreateDelegationRequest(factory, clock).execute(
            parent_run_id=run.id,
            target_agent_id=agent.id,
            objective="x",
            input_payload=None,
            expected_output_contract="y",
        )


def test_create_delegation_request_validates_parent_step_ownership() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, run = _setup(uow, clock, factory)
    other_task = CreateTask(factory, clock).execute(CreateTaskCommand("Other", ""))
    other_run = Run.new(id=RunId.new(), task_id=other_task.task_id, created_at=clock.now())
    uow.run_repo.add(other_run)
    foreign_step = RunStep.new(
        id=RunStepId.new(), run_id=other_run.id, name="s", position=0, created_at=clock.now()
    )
    uow.step_repo.add(foreign_step)

    with pytest.raises(EntityConflict):
        CreateDelegationRequest(factory, clock).execute(
            parent_run_id=run.id,
            target_agent_id=agent.id,
            objective="x",
            input_payload=None,
            expected_output_contract="y",
            parent_run_step_id=foreign_step.id,
        )


def test_create_delegation_request_rejects_missing_step() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, run = _setup(uow, clock, factory)
    with pytest.raises(RunStepNotFound):
        CreateDelegationRequest(factory, clock).execute(
            parent_run_id=run.id,
            target_agent_id=agent.id,
            objective="x",
            input_payload=None,
            expected_output_contract="y",
            parent_run_step_id=RunStepId.new(),
        )


def test_get_delegation_request_raises_not_found() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(DelegationRequestNotFound):
        GetDelegationRequest(factory).execute(DelegationRequestId.new())


def test_list_delegations_for_run_requires_existing_run() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(RunNotFound):
        ListDelegationsForRun(factory).execute(RunId.new())


def _claim_parent(uow: FakeUnitOfWork, run: Run) -> int:
    run.start(T0)
    uow.run_repo.save(run)
    uow.work_queue_repo.enqueue(run.id, T0, T0)
    assert uow.work_queue_repo.try_claim(
        run.id, "parent-worker", "parent-token", T0, T0 + timedelta(minutes=1)
    )
    item = uow.work_queue_repo.get(run.id)
    assert item is not None
    return item.claim_generation


def test_claim_fenced_dispatch_materializes_one_normal_child_execution() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)

    request = DispatchDelegation(factory, clock, _registry(), max_delegations_per_run=4).execute(
        parent_run_id=parent.id,
        worker_id="parent-worker",
        claim_token="parent-token",
        claim_generation=generation,
        target_agent_key="coder",
        objective="research the change",
        input_payload={"scope": "delegation"},
        expected_output_contract="Return evidence.",
    )

    assert request.status.value == "dispatched"
    assert request.child_task_id is not None
    assert request.child_run_id is not None
    assert parent.status.value == "waiting_for_delegation"
    assert parent.delegation_request_id == request.id
    assert parent.id not in uow.work_queue_repo.items
    child = uow.run_repo.get(request.child_run_id)
    assert child is not None
    assert child.execution_id == child.id
    assert child.id in uow.work_queue_repo.items
    assert uow.task_agent_binding_repo.items[request.child_task_id].agent_id == agent.id
    assert [event.type for event in uow.event_store.appended][-2:] == [
        RunEventType.DELEGATION_DISPATCHED,
        RunEventType.RUN_WAITING_FOR_DELEGATION,
    ]

    child_claimed = uow.work_queue_repo.try_claim(
        child.id, "child-worker", "child-token", T0, T0 + timedelta(minutes=1)
    )
    assert child_claimed
    child_item = uow.work_queue_repo.get(child.id)
    assert child_item is not None
    resolution = ResolveRunAgent(factory, clock, _registry()).execute(
        child.id, "child-worker", "child-token", child_item.claim_generation
    )
    assert resolution is not None
    assert resolution.agent_id == agent.id


def test_stale_parent_claim_creates_no_delegation_state() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    _agent, parent = _setup(uow, clock, factory)
    _claim_parent(uow, parent)
    before = (
        len(uow.task_repo.items),
        len(uow.run_repo.items),
        len(uow.delegation_request_repo.items),
    )

    with pytest.raises(ClaimLost):
        DispatchDelegation(factory, clock, _registry()).execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="stale-token",
            claim_generation=1,
            target_agent_key="coder",
            objective="research the change",
            input_payload={},
            expected_output_contract="Return evidence.",
        )

    assert (
        len(uow.task_repo.items),
        len(uow.run_repo.items),
        len(uow.delegation_request_repo.items),
    ) == before


def test_dispatch_rejects_unavailable_target_and_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    dispatch = DispatchDelegation(factory, clock, _registry())

    with pytest.raises(EntityConflict, match="delegation_target_not_found"):
        dispatch.execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key="missing",
            objective="work",
            input_payload={},
            expected_output_contract="result",
        )

    DisableAgent(factory, clock).execute(agent.id)
    with pytest.raises(EntityConflict, match="delegation_target_unavailable"):
        dispatch.execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key="coder",
            objective="work",
            input_payload={},
            expected_output_contract="result",
        )


def test_dispatch_rejects_missing_target_revision_and_runtime() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    dispatch = DispatchDelegation(factory, clock, _registry())
    uow.agent_revision_repo.items.clear()

    with pytest.raises(EntityConflict, match="delegation_target_revision_unavailable"):
        dispatch.execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key="coder",
            objective="work",
            input_payload={},
            expected_output_contract="result",
        )

    uow.agent_repo.items.clear()
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    with pytest.raises(EntityConflict, match="delegation_target_runtime_unavailable"):
        DispatchDelegation(factory, clock, BrainRuntimeRegistry()).execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key=agent.key,
            objective="work",
            input_payload={},
            expected_output_contract="result",
        )


def test_dispatch_rejects_invalid_parent_step_before_materialization() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    dispatch = DispatchDelegation(factory, clock, _registry())

    with pytest.raises(RunStepNotFound):
        dispatch.execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key=agent.key,
            objective="work",
            input_payload={},
            expected_output_contract="result",
            parent_run_step_id=RunStepId.new(),
        )

    other_task = CreateTask(factory, clock).execute(CreateTaskCommand("Other", ""))
    other_run = Run.new(id=RunId.new(), task_id=other_task.task_id, created_at=clock.now())
    uow.run_repo.add(other_run)
    foreign_step = RunStep.new(
        id=RunStepId.new(), run_id=other_run.id, name="foreign", position=0, created_at=clock.now()
    )
    uow.step_repo.add(foreign_step)
    with pytest.raises(EntityConflict, match="parent_run_step_id must belong"):
        dispatch.execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key=agent.key,
            objective="work",
            input_payload={},
            expected_output_contract="result",
            parent_run_step_id=foreign_step.id,
        )


def test_nested_dispatch_within_depth_bound_inherits_root_lineage() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    incoming = DelegationRequest.new(
        id=DelegationRequestId.new(),
        parent_run_id=RunId.new(),
        target_agent_id=agent.id,
        objective="incoming",
        input_payload={},
        expected_output_contract="result",
        created_at=T0,
    )
    incoming.dispatch(parent.task_id, parent.id, T0)
    uow.delegation_request_repo.add(incoming)

    request = DispatchDelegation(factory, clock, _registry()).execute(
        parent_run_id=parent.id,
        worker_id="parent-worker",
        claim_token="parent-token",
        claim_generation=generation,
        target_agent_key="coder",
        objective="grandchild",
        input_payload={},
        expected_output_contract="result",
    )
    assert request.depth == 2
    assert request.root_delegation_id == incoming.id
    assert incoming.depth == 1 and incoming.root_delegation_id == incoming.id
    assert request.status.value == "dispatched"
    assert parent.status.value == "waiting_for_delegation"
    assert parent.delegation_request_id == request.id
    assert request.child_task_id is not None
    assert uow.task_agent_binding_repo.items[request.child_task_id].agent_id == agent.id


def test_nested_dispatch_beyond_depth_bound_fails_closed() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    incoming = DelegationRequest.new(
        id=DelegationRequestId.new(),
        parent_run_id=RunId.new(),
        target_agent_id=agent.id,
        objective="incoming at the boundary",
        input_payload={},
        expected_output_contract="result",
        created_at=T0,
        root_delegation_id=DelegationRequestId.new(),
        depth=3,
    )
    incoming.dispatch(parent.task_id, parent.id, T0)
    uow.delegation_request_repo.add(incoming)
    before = (
        len(uow.task_repo.items),
        len(uow.run_repo.items),
        len(uow.delegation_request_repo.items),
        len(uow.work_queue_repo.items),
        len(uow.event_store.appended),
    )

    with pytest.raises(EntityConflict, match="delegation_depth_exhausted"):
        DispatchDelegation(factory, clock, _registry()).execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key="coder",
            objective="grandchild beyond the boundary",
            input_payload={},
            expected_output_contract="result",
        )
    assert (
        len(uow.task_repo.items),
        len(uow.run_repo.items),
        len(uow.delegation_request_repo.items),
        len(uow.work_queue_repo.items),
        len(uow.event_store.appended),
    ) == before
    assert parent.status.value == "running"
    assert parent.delegation_request_id is None


def test_delegation_budget_and_input_limits_fail_closed() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent, parent = _setup(uow, clock, factory)
    generation = _claim_parent(uow, parent)
    for _ in range(4):
        request = DelegationRequest.new(
            id=DelegationRequestId.new(),
            parent_run_id=parent.id,
            target_agent_id=agent.id,
            objective="already dispatched",
            input_payload={},
            expected_output_contract="result",
            created_at=T0,
        )
        request.dispatch(TaskId.new(), RunId.new(), T0)
        request.succeed(T0)
        uow.delegation_request_repo.add(request)

    with pytest.raises(EntityConflict, match="delegation_budget_exhausted"):
        DispatchDelegation(factory, clock, _registry()).execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key="coder",
            objective="over budget",
            input_payload={},
            expected_output_contract="result",
        )

    uow.delegation_request_repo.items.clear()
    with pytest.raises(EntityConflict, match="delegation_input_too_large"):
        DispatchDelegation(factory, clock, _registry()).execute(
            parent_run_id=parent.id,
            worker_id="parent-worker",
            claim_token="parent-token",
            claim_generation=generation,
            target_agent_key="coder",
            objective="large input",
            input_payload={"data": "x" * 20_000},
            expected_output_contract="result",
        )

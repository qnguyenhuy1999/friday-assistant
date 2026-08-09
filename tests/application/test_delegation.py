from __future__ import annotations

import pytest

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
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
    GetDelegationRequest,
    ListDelegationsForRun,
)
from friday.application.errors import (
    AgentNotFound,
    DelegationRequestNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.domain.agent import AgentRevisionSourceKind
from friday.domain.approval import ApprovalCategory, ApprovalSubjectKind
from friday.domain.identifiers import AgentId, DelegationRequestId, RunId, RunStepId
from friday.domain.run import Run
from friday.domain.step import RunStep
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


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

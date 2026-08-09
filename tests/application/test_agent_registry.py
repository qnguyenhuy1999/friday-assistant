from __future__ import annotations

import pytest

from friday.application.agent_registry import (
    ActivateAgentRevision,
    ArchiveAgent,
    CreateAgent,
    CreateAgentRevision,
    DisableAgent,
    GetAgent,
    ReplaceTaskAgent,
    ResolveRunAgent,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.commands import CreateTaskCommand
from friday.application.create_task import CreateTask
from friday.application.errors import (
    AgentNotFound,
    AgentRevisionNotFound,
    ClaimLost,
    EntityConflict,
    InvalidBrainRuntimeConfig,
    TaskNotFound,
    UnknownBrainRuntimeKind,
)
from friday.domain.agent import (
    AgentRevisionSourceKind,
    AgentStatus,
    RunAgentResolution,
    TaskAgentBinding,
)
from friday.domain.delegation import DelegationRequest
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import (
    AgentId,
    AgentRevisionId,
    DelegationRequestId,
    RunAgentResolutionId,
    RunId,
    TaskId,
)
from friday.domain.json_value import JsonValue
from friday.domain.run import Run
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork
from tests.application.resolve_helpers import resolve_run_agent_without_claim


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def test_create_agent_revision_rejects_unregistered_runtime_kind() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(UnknownBrainRuntimeKind):
        CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=agent.id,
            instructions="be helpful",
            runtime_kind="totally_unknown",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
    assert uow.agent_revision_repo.items == {}


@pytest.mark.parametrize(
    "runtime_config",
    [
        {"model": "unsafe-to-persist"},
        {"command": "rm -rf /"},
        {"env": {"API_KEY": "secret"}},
    ],
)
def test_create_agent_revision_rejects_runtime_authority_configuration(
    runtime_config: JsonValue,
) -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(InvalidBrainRuntimeConfig):
        CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=agent.id,
            instructions="be helpful",
            runtime_kind="claude_cli",
            runtime_config=runtime_config,
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
    assert uow.agent_revision_repo.items == {}


def test_create_agent_revision_rejects_overly_complex_runtime_config() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(InvalidBrainRuntimeConfig):
        CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=agent.id,
            instructions="be helpful",
            runtime_kind="claude_cli",
            runtime_config={"nested": [[[[["too deep"]]]]]},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )
    assert uow.agent_revision_repo.items == {}


def test_revision_lifecycle_is_explicit_and_immutable() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    registry = _registry()
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    one = CreateAgentRevision(factory, clock, registry).execute(
        agent_id=agent.id,
        instructions="be helpful",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    assert agent.active_revision_id is None
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=one.id)
    assert agent.active_revision_id == one.id
    two = CreateAgentRevision(factory, clock, registry).execute(
        agent_id=agent.id,
        instructions="be more helpful",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    assert two.version == 2
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=two.id)
    assert agent.active_revision_id == two.id
    assert uow.agent_revision_repo.get(one.id) == one


def test_disabled_agent_can_be_reactivated_with_a_valid_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    revision = CreateAgentRevision(factory, clock, _registry()).execute(
        agent_id=agent.id,
        instructions="be helpful",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    DisableAgent(factory, clock).execute(agent.id)
    reactivated = ActivateAgentRevision(factory, clock).execute(
        agent_id=agent.id, revision_id=revision.id
    )
    assert reactivated.status is AgentStatus.ACTIVE
    assert reactivated.active_revision_id == revision.id


def test_cross_agent_activation_fails_closed() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    registry = _registry()
    a = CreateAgent(factory, clock).execute(key="a", display_name="A", description="")
    b = CreateAgent(factory, clock).execute(key="b", display_name="B", description="")
    rev = CreateAgentRevision(factory, clock, registry).execute(
        agent_id=b.id,
        instructions="x",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    with pytest.raises(DomainValidationError):
        ActivateAgentRevision(factory, clock).execute(agent_id=a.id, revision_id=rev.id)


def test_archived_agent_cannot_receive_revisions() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    ArchiveAgent(factory, clock).execute(agent.id)
    with pytest.raises(EntityConflict):
        CreateAgentRevision(factory, clock, _registry()).execute(
            agent_id=agent.id,
            instructions="x",
            runtime_kind="claude_cli",
            runtime_config={},
            source_kind=AgentRevisionSourceKind.OPERATOR,
        )


def test_get_agent_raises_not_found_for_missing_id() -> None:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(AgentNotFound):
        GetAgent(factory).execute(AgentId.new())


def test_agent_lifecycle_queries_fail_closed_for_missing_entities() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(AgentNotFound):
        ActivateAgentRevision(factory, clock).execute(
            agent_id=AgentId.new(), revision_id=AgentRevisionId.new()
        )
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(AgentRevisionNotFound):
        ActivateAgentRevision(factory, clock).execute(
            agent_id=agent.id, revision_id=AgentRevisionId.new()
        )
    with pytest.raises(AgentNotFound):
        GetAgent(factory).list_revisions(AgentId.new())
    with pytest.raises(AgentNotFound):
        DisableAgent(factory, clock).execute(AgentId.new())


def _active_agent(factory: CountingUnitOfWorkFactory, clock: FakeClock):  # type: ignore[no-untyped-def]
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    revision = CreateAgentRevision(factory, clock, _registry()).execute(
        agent_id=agent.id,
        instructions="be helpful",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
    return agent, revision


def test_replace_task_agent_binds_and_unbinds() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    agent, _ = _active_agent(factory, clock)
    binding = ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=agent.id)
    assert binding is not None and binding.agent_id == agent.id
    cleared = ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=None)
    assert cleared is None
    assert uow.task_agent_binding_repo.get(task.task_id) is None


def test_replace_task_agent_requires_active_agent_with_active_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    with pytest.raises(EntityConflict):
        ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=agent.id)


def test_replace_task_agent_rejects_missing_task_or_agent() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    with pytest.raises(TaskNotFound):
        ReplaceTaskAgent(factory, clock).execute(task_id=TaskId.new(), agent_id=None)
    with pytest.raises(AgentNotFound):
        ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=AgentId.new())


def test_resolve_delegated_run_rejects_missing_or_unresolvable_binding() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("child", ""))
    target, revision = _active_agent(factory, clock)
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)
    request = DelegationRequest.new(
        id=DelegationRequestId.new(),
        parent_run_id=RunId.new(),
        target_agent_id=target.id,
        objective="owned work",
        input_payload={},
        expected_output_contract="result",
        created_at=clock.now(),
    )
    request.dispatch(task.task_id, run.id, clock.now())
    uow.delegation_request_repo.add(request)

    with pytest.raises(EntityConflict, match="delegated_child_agent_binding_missing"):
        resolve_run_agent_without_claim(factory, clock, run.id)

    uow.task_agent_binding_repo.replace(
        task.task_id, TaskAgentBinding(task.task_id, target.id, clock.now())
    )
    target._active_revision_id = None
    with pytest.raises(EntityConflict, match="bound agent is no longer resolvable"):
        resolve_run_agent_without_claim(factory, clock, run.id)

    target._active_revision_id = revision.id
    uow.agent_revision_repo.items.clear()
    with pytest.raises(EntityConflict, match="active agent revision does not exist"):
        resolve_run_agent_without_claim(factory, clock, run.id)


def test_resolve_delegated_run_rejects_existing_mismatched_freeze() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("child", ""))
    target, _ = _active_agent(factory, clock)
    bound, bound_revision = _active_agent(factory, clock)
    assert target.id != bound.id
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)
    request = DelegationRequest.new(
        id=DelegationRequestId.new(),
        parent_run_id=RunId.new(),
        target_agent_id=target.id,
        objective="owned work",
        input_payload={},
        expected_output_contract="result",
        created_at=clock.now(),
    )
    request.dispatch(task.task_id, run.id, clock.now())
    uow.delegation_request_repo.add(request)
    uow.run_agent_resolution_repo.add(
        RunAgentResolution(
            RunAgentResolutionId.new(),
            run.id,
            bound.id,
            bound_revision.id,
            clock.now(),
        )
    )
    assert uow.delegation_request_repo.get_for_child_execution(run.execution_id) is request

    with pytest.raises(EntityConflict, match="delegation_target_agent_mismatch"):
        resolve_run_agent_without_claim(factory, clock, run.id)


def test_resolve_run_agent_freezes_bound_agent_once() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    agent, revision = _active_agent(factory, clock)
    ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=agent.id)
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)

    resolution = resolve_run_agent_without_claim(factory, clock, run.id)
    assert resolution is not None
    assert resolution.agent_id == agent.id
    assert resolution.revision_id == revision.id

    again = resolve_run_agent_without_claim(factory, clock, run.id)
    assert again == resolution


def test_resolve_run_agent_is_none_when_task_has_no_binding() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)

    resolution = resolve_run_agent_without_claim(factory, clock, run.id)
    assert resolution is None
    assert uow.run_agent_resolution_repo.get(run.id) is None


def test_resolve_run_agent_requires_exact_active_claim() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("T", ""))
    agent, _ = _active_agent(factory, clock)
    ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=agent.id)
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)

    with pytest.raises(ClaimLost):
        ResolveRunAgent(factory, clock, _registry()).execute(run.id, "worker", "token", 1)
    assert uow.run_agent_resolution_repo.get(run.id) is None


def test_resolve_delegated_run_rejects_binding_target_mismatch_before_freeze() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task = CreateTask(factory, clock).execute(CreateTaskCommand("child", ""))
    target, _ = _active_agent(factory, clock)
    bound, _ = _active_agent(factory, clock)
    ReplaceTaskAgent(factory, clock).execute(task_id=task.task_id, agent_id=bound.id)
    run = Run.new(id=RunId.new(), task_id=task.task_id, created_at=clock.now())
    uow.run_repo.add(run)
    request = DelegationRequest.new(
        id=DelegationRequestId.new(),
        parent_run_id=RunId.new(),
        target_agent_id=target.id,
        objective="owned work",
        input_payload={},
        expected_output_contract="result",
        created_at=clock.now(),
    )
    request.dispatch(task.task_id, run.id, clock.now())
    uow.delegation_request_repo.add(request)

    with pytest.raises(EntityConflict, match="delegation_target_agent_mismatch"):
        resolve_run_agent_without_claim(factory, clock, run.id)
    assert uow.run_agent_resolution_repo.get(run.id) is None

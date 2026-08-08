"""RetryFailedRun copies an already-frozen source RunAgentResolution onto the
retry exactly, mirroring the existing RunSkillResolution copy behavior. An
unresolved source produces an unresolved retry."""

from __future__ import annotations

from datetime import timedelta

from friday.application.agent_registry import (
    ActivateAgentRevision,
    CreateAgent,
    CreateAgentRevision,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.commands import FailRunCommand, RetryFailedRunCommand, StartQueuedRunCommand
from friday.application.lifecycle import FailRun, RetryFailedRun, StartQueuedRun
from friday.domain.agent import AgentRevisionSourceKind, RunAgentResolution
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunAgentResolutionId, RunId, TaskId
from friday.domain.run import Run
from friday.domain.task import Task
from tests.application.fakes import T0, CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

FAILURE = Failure("x", "failed", True, FailureCause.RUNTIME)


def _registry() -> BrainRuntimeRegistry:
    registry = BrainRuntimeRegistry()
    registry.register("claude_cli", lambda: None)  # type: ignore[arg-type,return-value]
    return registry


def _prepared() -> tuple[FakeUnitOfWork, CountingUnitOfWorkFactory, Task, Run]:
    uow = FakeUnitOfWork()
    factory = CountingUnitOfWorkFactory(uow)
    task = Task.new(id=TaskId.new(), title="t", description="", created_at=T0)
    task.start(T0)
    uow.task_repo.add(task)
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    uow.run_repo.add(run)
    return uow, factory, task, run


def test_retry_copies_exact_source_run_agent_resolution() -> None:
    uow, factory, task, run = _prepared()
    clock = FakeClock(T0 + timedelta(minutes=1))
    agent = CreateAgent(factory, clock).execute(key="coder", display_name="Coder", description="")
    revision = CreateAgentRevision(factory, clock, _registry()).execute(
        agent_id=agent.id,
        instructions="be helpful",
        runtime_kind="claude_cli",
        runtime_config={},
        source_kind=AgentRevisionSourceKind.OPERATOR,
    )
    ActivateAgentRevision(factory, clock).execute(agent_id=agent.id, revision_id=revision.id)
    source_resolution = RunAgentResolution(
        RunAgentResolutionId.new(), run.id, agent.id, revision.id, clock.now()
    )
    uow.run_agent_resolution_repo.add(source_resolution)

    StartQueuedRun(factory, clock).execute(StartQueuedRunCommand(run.id))
    FailRun(factory, clock).execute(FailRunCommand(run.id, FAILURE))
    retry = RetryFailedRun(factory, clock).execute(RetryFailedRunCommand(run.id))

    copied = uow.run_agent_resolution_repo.get(retry.run_id)
    assert copied is not None
    assert copied.run_id == retry.run_id
    assert copied.agent_id == agent.id
    assert copied.revision_id == revision.id
    assert copied.id != source_resolution.id


def test_retry_of_unresolved_source_stays_unresolved() -> None:
    uow, factory, task, run = _prepared()
    clock = FakeClock(T0 + timedelta(minutes=1))
    StartQueuedRun(factory, clock).execute(StartQueuedRunCommand(run.id))
    FailRun(factory, clock).execute(FailRunCommand(run.id, FAILURE))
    retry = RetryFailedRun(factory, clock).execute(RetryFailedRunCommand(run.id))
    assert uow.run_agent_resolution_repo.get(retry.run_id) is None

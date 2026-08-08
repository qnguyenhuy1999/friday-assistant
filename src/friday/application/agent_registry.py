from __future__ import annotations

from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.errors import (
    AgentNotFound,
    AgentRevisionNotFound,
    ClaimLost,
    EntityConflict,
    RunNotFound,
    TaskNotFound,
    UnknownBrainRuntimeKind,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    Agent,
    AgentId,
    AgentRevision,
    AgentRevisionId,
    AgentRevisionSourceKind,
    AgentStatus,
    RunAgentResolution,
    RunAgentResolutionId,
    RunId,
    TaskAgentBinding,
    TaskId,
)
from friday.domain.json_value import JsonValue


class CreateAgent:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, key: str, display_name: str, description: str) -> Agent:
        agent = Agent.new(
            id=AgentId.new(),
            key=key,
            display_name=display_name,
            description=description,
            created_at=self._clock.now(),
        )
        with self._uow_factory() as uow:
            uow.agents.add(agent)
            uow.commit()
        return agent


class CreateAgentRevision:
    """Validates `runtime_kind` against the code-owned brain runtime
    registry before persisting: a revision naming an unregistered kind must
    never reach the database, so no Run can later fail closed on a frozen
    identity it cannot execute."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        runtime_registry: BrainRuntimeRegistry,
    ) -> None:
        self._uow_factory, self._clock = uow_factory, clock
        self._runtime_registry = runtime_registry

    def execute(
        self,
        *,
        agent_id: AgentId,
        instructions: str,
        runtime_kind: str,
        runtime_config: JsonValue,
        source_kind: AgentRevisionSourceKind,
    ) -> AgentRevision:
        if not self._runtime_registry.is_registered(runtime_kind):
            raise UnknownBrainRuntimeKind(runtime_kind)
        with self._uow_factory() as uow:
            agent = uow.agents.get(agent_id)
            if agent is None:
                raise AgentNotFound(agent_id)
            if agent.status is AgentStatus.ARCHIVED:
                raise EntityConflict("archived agent cannot receive revisions")
            revision = AgentRevision.new(
                id=AgentRevisionId.new(),
                agent_id=agent_id,
                version=uow.agent_revisions.next_version(agent_id),
                instructions=instructions,
                runtime_kind=runtime_kind,
                runtime_config=runtime_config,
                source_kind=source_kind,
                created_at=self._clock.now(),
            )
            uow.agent_revisions.add(revision)
            uow.commit()
            return revision


class ActivateAgentRevision:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, agent_id: AgentId, revision_id: AgentRevisionId) -> Agent:
        with self._uow_factory() as uow:
            agent = uow.agents.get(agent_id)
            if agent is None:
                raise AgentNotFound(agent_id)
            revision = uow.agent_revisions.get(revision_id)
            if revision is None:
                raise AgentRevisionNotFound(revision_id)
            agent.activate(revision, self._clock.now())
            uow.agents.save(agent)
            uow.commit()
            return agent


class _AgentLifecycle:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def _change(self, agent_id: AgentId, action: str) -> Agent:
        with self._uow_factory() as uow:
            agent = uow.agents.get(agent_id)
            if agent is None:
                raise AgentNotFound(agent_id)
            getattr(agent, action)(self._clock.now())
            uow.agents.save(agent)
            uow.commit()
            return agent


class DisableAgent(_AgentLifecycle):
    def execute(self, agent_id: AgentId) -> Agent:
        return self._change(agent_id, "disable")


class ArchiveAgent(_AgentLifecycle):
    def execute(self, agent_id: AgentId) -> Agent:
        return self._change(agent_id, "archive")


class GetAgent:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, agent_id: AgentId) -> Agent:
        with self._uow_factory() as uow:
            agent = uow.agents.get(agent_id)
            if agent is None:
                raise AgentNotFound(agent_id)
            return agent

    def list_revisions(self, agent_id: AgentId) -> list[AgentRevision]:
        with self._uow_factory() as uow:
            if uow.agents.get(agent_id) is None:
                raise AgentNotFound(agent_id)
            return uow.agent_revisions.list_for_agent(agent_id)


class ReplaceTaskAgent:
    """Bind (or unbind) the single Agent a Task's future unresolved Runs use.

    Rebinding never touches an already-resolved Run: `ResolveRunAgent` froze
    that Run's exact `(agent_id, revision_id)` for good, so only Runs that
    have not yet resolved observe the new binding."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, task_id: TaskId, agent_id: AgentId | None) -> TaskAgentBinding | None:
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            binding: TaskAgentBinding | None = None
            if agent_id is not None:
                agent = uow.agents.get(agent_id)
                if agent is None:
                    raise AgentNotFound(agent_id)
                if agent.status is not AgentStatus.ACTIVE or agent.active_revision_id is None:
                    raise EntityConflict(
                        "task bindings require active agents with an active revision"
                    )
                binding = TaskAgentBinding(task_id, agent_id, self._clock.now())
            uow.task_agent_bindings.replace(task_id, binding)
            uow.commit()
            return binding


class ResolveRunAgent:
    """Freeze a Run's Agent identity once, exactly like `ResolveRunSkills`
    freezes Skill bindings. A Task with no Agent binding resolves to nothing
    — Friday's existing default Claude-based behavior applies, and no fake
    Agent identity is ever fabricated for it."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
    ) -> RunAgentResolution | None:
        try:
            with self._uow_factory() as uow:
                run = uow.runs.get(run_id)
                if run is None:
                    raise RunNotFound(run_id)
                if not uow.work_queue.is_claim_active(
                    run.id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("agent resolution requires an exact active worker claim")
                existing = uow.run_agent_resolutions.get(run.id)
                if existing is not None:
                    return existing

                binding = uow.task_agent_bindings.get(run.task_id)
                if binding is None:
                    return None

                agent = uow.agents.get(binding.agent_id)
                if (
                    agent is None
                    or agent.status is not AgentStatus.ACTIVE
                    or agent.active_revision_id is None
                ):
                    raise EntityConflict("bound agent is no longer resolvable")

                resolution = RunAgentResolution(
                    RunAgentResolutionId.new(),
                    run.id,
                    agent.id,
                    agent.active_revision_id,
                    self._clock.now(),
                )
                # The atomic conditional INSERT is the sole publication
                # boundary: it re-reads the queue row under the exact claim
                # so a claim that lapsed after the read above can never
                # publish the freeze marker, mirroring ResolveRunSkills.
                if not uow.run_agent_resolutions.add_if_claimed(
                    resolution,
                    worker_id,
                    claim_token,
                    claim_generation,
                    self._clock.now(),
                ):
                    if uow.run_agent_resolutions.get(run.id) is not None:
                        raise EntityConflict("another resolver won the freeze race")
                    raise ClaimLost("agent resolution claim is stale or expired")
                if not uow.work_queue.is_claim_active(
                    run.id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("agent resolution claim expired before commit")
                uow.commit()
                return resolution
        except EntityConflict:
            # Two valid resolvers may race on the unique resolution marker.
            # The loser rolls back and reloads the winner under its own
            # still-active claim, exactly like ResolveRunSkills.
            with self._uow_factory() as uow:
                if not uow.work_queue.is_claim_active(
                    run_id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("agent resolution claim is stale or expired") from None
                winner = uow.run_agent_resolutions.get(run_id)
                if winner is not None:
                    return winner
            raise

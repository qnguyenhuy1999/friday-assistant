from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from apps.api.dependencies import get_brain_runtime_registry, get_clock, get_uow_factory
from apps.api.schemas.agents import (
    AgentPageResponse,
    AgentResponse,
    AgentRevisionResponse,
    CreateAgentBody,
    CreateAgentRevisionBody,
)
from friday.application.agent_registry import (
    ActivateAgentRevision,
    ArchiveAgent,
    CreateAgent,
    CreateAgentRevision,
    DisableAgent,
    GetAgent,
)
from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import Agent, AgentId, AgentRevision, AgentRevisionId, AgentRevisionSourceKind

router = APIRouter(prefix="/v1/agents", tags=["agents"])
Uow = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDep = Annotated[Clock, Depends(get_clock)]
RegistryDep = Annotated[BrainRuntimeRegistry, Depends(get_brain_runtime_registry)]


def _agent(x: Agent) -> AgentResponse:
    return AgentResponse(
        id=str(x.id),
        key=x.key,
        display_name=x.display_name,
        description=x.description,
        status=x.status.value,
        active_revision_id=str(x.active_revision_id) if x.active_revision_id else None,
        created_at=x.created_at,
        updated_at=x.updated_at,
    )


def _revision(x: AgentRevision) -> AgentRevisionResponse:
    return AgentRevisionResponse(
        id=str(x.id),
        agent_id=str(x.agent_id),
        version=x.version,
        instructions=x.instructions,
        runtime_kind=x.runtime_kind,
        runtime_config=x.runtime_config,
        content_sha256=x.content_sha256,
        source_kind=x.source_kind.value,
        created_at=x.created_at,
    )


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgent",
)
def create(body: CreateAgentBody, uow: Uow, clock: ClockDep) -> AgentResponse:
    return _agent(
        CreateAgent(uow, clock).execute(
            key=body.key, display_name=body.display_name, description=body.description
        )
    )


@router.get("", response_model=AgentPageResponse, operation_id="listAgents")
def list_agents(uow: Uow) -> AgentPageResponse:
    with uow() as tx:
        return AgentPageResponse(items=[_agent(x) for x in tx.agents.list(100)])


@router.get("/{agent_id}", response_model=AgentResponse, operation_id="getAgent")
def get_agent(agent_id: UUID, uow: Uow) -> AgentResponse:
    return _agent(GetAgent(uow).execute(AgentId.parse(str(agent_id))))


@router.post(
    "/{agent_id}/revisions",
    response_model=AgentRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgentRevision",
)
def create_revision(
    agent_id: UUID,
    body: CreateAgentRevisionBody,
    uow: Uow,
    clock: ClockDep,
    registry: RegistryDep,
) -> AgentRevisionResponse:
    return _revision(
        CreateAgentRevision(uow, clock, registry).execute(
            agent_id=AgentId.parse(str(agent_id)),
            instructions=body.instructions,
            runtime_kind=body.runtime_kind,
            runtime_config=body.runtime_config,
            source_kind=AgentRevisionSourceKind(body.source_kind),
        )
    )


@router.get(
    "/{agent_id}/revisions",
    response_model=list[AgentRevisionResponse],
    operation_id="listAgentRevisions",
)
def list_revisions(agent_id: UUID, uow: Uow) -> list[AgentRevisionResponse]:
    return [_revision(x) for x in GetAgent(uow).list_revisions(AgentId.parse(str(agent_id)))]


@router.post(
    "/{agent_id}/revisions/{revision_id}/activate",
    response_model=AgentResponse,
    operation_id="activateAgentRevision",
)
def activate(agent_id: UUID, revision_id: UUID, uow: Uow, clock: ClockDep) -> AgentResponse:
    return _agent(
        ActivateAgentRevision(uow, clock).execute(
            agent_id=AgentId.parse(str(agent_id)),
            revision_id=AgentRevisionId.parse(str(revision_id)),
        )
    )


@router.post("/{agent_id}/disable", response_model=AgentResponse, operation_id="disableAgent")
def disable(agent_id: UUID, uow: Uow, clock: ClockDep) -> AgentResponse:
    return _agent(DisableAgent(uow, clock).execute(AgentId.parse(str(agent_id))))


@router.post("/{agent_id}/archive", response_model=AgentResponse, operation_id="archiveAgent")
def archive(agent_id: UUID, uow: Uow, clock: ClockDep) -> AgentResponse:
    return _agent(ArchiveAgent(uow, clock).execute(AgentId.parse(str(agent_id))))

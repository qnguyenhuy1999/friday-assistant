"""Delegation contract use cases: persistence and validation only. No child
Run is dispatched here — see friday.domain.delegation for the invariants
this Step 1 slice must never violate (a target Agent grants no authority, a
parent approval cannot authorize a future child execution)."""

from __future__ import annotations

from friday.application.errors import (
    AgentNotFound,
    DelegationRequestNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import AgentId, AgentStatus, DelegationRequest, DelegationRequestId, RunId
from friday.domain.identifiers import RunStepId
from friday.domain.json_value import JsonValue


class CreateDelegationRequest:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        *,
        parent_run_id: RunId,
        target_agent_id: AgentId,
        objective: str,
        input_payload: JsonValue,
        expected_output_contract: str,
        parent_run_step_id: RunStepId | None = None,
    ) -> DelegationRequest:
        with self._uow_factory() as uow:
            if uow.runs.get(parent_run_id) is None:
                raise RunNotFound(parent_run_id)
            if parent_run_step_id is not None:
                step = uow.steps.get(parent_run_step_id)
                if step is None:
                    raise RunStepNotFound(parent_run_step_id)
                if step.run_id != parent_run_id:
                    raise EntityConflict("parent_run_step_id must belong to the parent run")
            agent = uow.agents.get(target_agent_id)
            if agent is None:
                raise AgentNotFound(target_agent_id)
            if agent.status is not AgentStatus.ACTIVE or agent.active_revision_id is None:
                raise EntityConflict(
                    "delegation target requires an active agent with an active revision"
                )
            request = DelegationRequest.new(
                id=DelegationRequestId.new(),
                parent_run_id=parent_run_id,
                target_agent_id=target_agent_id,
                objective=objective,
                input_payload=input_payload,
                expected_output_contract=expected_output_contract,
                created_at=self._clock.now(),
                parent_run_step_id=parent_run_step_id,
            )
            uow.delegation_requests.add(request)
            uow.commit()
            return request


class GetDelegationRequest:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, delegation_id: DelegationRequestId) -> DelegationRequest:
        with self._uow_factory() as uow:
            request = uow.delegation_requests.get(delegation_id)
            if request is None:
                raise DelegationRequestNotFound(delegation_id)
            return request


class ListDelegationsForRun:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, run_id: RunId) -> list[DelegationRequest]:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            return uow.delegation_requests.list_for_run(run_id)

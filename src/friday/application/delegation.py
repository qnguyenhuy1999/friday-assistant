"""Delegation contract use cases: persistence and validation only. No child
Run is dispatched here — see friday.domain.delegation for the invariants
this Step 1 slice must never violate (a target Agent grants no authority, a
parent approval cannot authorize a future child execution)."""

from __future__ import annotations

import json
from datetime import datetime

from friday.application.brain_runtime_registry import BrainRuntimeRegistry
from friday.application.errors import (
    AgentNotFound,
    ClaimLost,
    DelegationRequestNotFound,
    EntityConflict,
    RunNotFound,
    RunStepNotFound,
)
from friday.application.lifecycle_events import LifecycleEvents
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.start_run import StartRun
from friday.domain import (
    AgentId,
    AgentStatus,
    DelegationRequest,
    DelegationRequestId,
    RunId,
    TaskAgentBinding,
)
from friday.domain.delegation import (
    MAX_DELEGATION_DEPTH,
    MAX_DELEGATIONS_PER_RUN,
    MAX_DELEGATIONS_PER_TREE,
)
from friday.domain.event import RunEventType
from friday.domain.identifiers import RunStepId, TaskId
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.run import RunStatus
from friday.domain.task import Task

MAX_DISPATCH_INPUT_BYTES = 16_384


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


class DispatchDelegation:
    """Claim-fenced parent -> child materialization.

    This is deliberately a single transaction. It does not call a nested use
    case that commits: StartRun.execute_in_uow only contributes the normal
    child Task/Run/work-item writes to this transaction.

    A delegated child Run may itself dispatch a further delegation: the
    incoming delegation is derived from the parent Run's canonical execution
    lineage, the new request inherits its root and depth, and depth beyond
    `max_delegation_depth` fails closed with `delegation_depth_exhausted`.
    The nested hop reuses this exact path — there is no second, nested or
    agent-to-agent dispatch mechanism, and no authority crosses the hop.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        runtime_registry: BrainRuntimeRegistry,
        *,
        max_delegations_per_run: int = MAX_DELEGATIONS_PER_RUN,
        max_delegations_per_tree: int = MAX_DELEGATIONS_PER_TREE,
        max_delegation_depth: int = MAX_DELEGATION_DEPTH,
    ) -> None:
        if max_delegations_per_run < 1:
            raise ValueError("max_delegations_per_run must be positive")
        if max_delegations_per_tree < 1:
            raise ValueError("max_delegations_per_tree must be positive")
        if max_delegation_depth < 1:
            raise ValueError("max_delegation_depth must be >= 1")
        self._uow_factory = uow_factory
        self._clock = clock
        self._runtime_registry = runtime_registry
        self._max_delegations_per_run = max_delegations_per_run
        self._max_delegations_per_tree = max_delegations_per_tree
        self._max_delegation_depth = max_delegation_depth

    def execute(
        self,
        *,
        parent_run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        target_agent_key: str,
        objective: str,
        input_payload: JsonValue,
        expected_output_contract: str,
        parent_run_step_id: RunStepId | None = None,
    ) -> DelegationRequest:
        with self._uow_factory() as uow:
            now = self._clock.now()
            self._require_claim(uow, parent_run_id, worker_id, claim_token, claim_generation, now)
            parent = uow.runs.get(parent_run_id)
            if parent is None:
                raise RunNotFound(parent_run_id)
            if parent.status is not RunStatus.RUNNING:
                raise EntityConflict("delegation parent must be running")
            incoming = uow.delegation_requests.get_for_child_execution(parent.execution_id)
            root_delegation_id: DelegationRequestId | None = None
            depth = 1
            if incoming is not None:
                assert incoming.depth is not None  # always set by __post_init__
                depth = incoming.depth + 1
                if depth > self._max_delegation_depth:
                    raise EntityConflict("delegation_depth_exhausted")
                root_delegation_id = incoming.root_delegation_id

            # The exact parent Run serializes every competing direct dispatch.
            # Nested dispatches additionally take the existing root request's
            # durable write fence before counting the whole tree.  These are
            # database fences, not process-local locks, and they remain held
            # through child Task/Run/queue/event materialization and commit.
            if (
                root_delegation_id is not None
                and not uow.delegation_requests.lock_tree_for_dispatch(root_delegation_id)
            ):
                raise EntityConflict("delegation_lineage_invalid")
            if not uow.runs.lock_for_delegation_dispatch(parent.id):
                raise RunNotFound(parent.id)

            if uow.delegation_requests.count_materialized_for_run(parent.id) >= (
                self._max_delegations_per_run
            ):
                raise EntityConflict("delegation_budget_exhausted")
            if (
                root_delegation_id is not None
                and uow.delegation_requests.count_materialized_for_tree(root_delegation_id)
                >= self._max_delegations_per_tree
            ):
                raise EntityConflict("delegation_budget_exhausted")

            normalized_input = ensure_json_value(
                input_payload, path="DelegationRequest.input_payload"
            )
            encoded_input = json.dumps(
                normalized_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            if len(encoded_input.encode("utf-8")) > MAX_DISPATCH_INPUT_BYTES:
                raise EntityConflict("delegation_input_too_large")
            if parent_run_step_id is not None:
                step = uow.steps.get(parent_run_step_id)
                if step is None:
                    raise RunStepNotFound(parent_run_step_id)
                if step.run_id != parent.id:
                    raise EntityConflict("parent_run_step_id must belong to the parent run")

            agent = uow.agents.get_by_key(target_agent_key)
            if agent is None:
                raise EntityConflict("delegation_target_not_found")
            if agent.status is not AgentStatus.ACTIVE or agent.active_revision_id is None:
                raise EntityConflict("delegation_target_unavailable")
            revision = uow.agent_revisions.get(agent.active_revision_id)
            if revision is None or revision.agent_id != agent.id:
                raise EntityConflict("delegation_target_revision_unavailable")
            if not self._runtime_registry.is_registered(revision.runtime_kind):
                raise EntityConflict("delegation_target_runtime_unavailable")
            try:
                self._runtime_registry.validate_runtime_config(
                    revision.runtime_kind, revision.runtime_config
                )
            except Exception as exc:
                raise EntityConflict("delegation_target_runtime_unavailable") from exc

            request = DelegationRequest.new(
                id=DelegationRequestId.new(),
                parent_run_id=parent.id,
                target_agent_id=agent.id,
                objective=objective,
                input_payload=normalized_input,
                expected_output_contract=expected_output_contract,
                created_at=now,
                parent_run_step_id=parent_run_step_id,
                root_delegation_id=root_delegation_id,
                depth=depth,
            )
            child_task = Task.new(
                id=TaskId.new(),
                title=f"Delegated: {request.objective}"[:4_000],
                description=request.objective,
                created_at=now,
            )
            uow.tasks.add(child_task)
            uow.task_agent_bindings.replace(
                child_task.id,
                TaskAgentBinding(child_task.id, agent.id, now),
            )
            child_result = StartRun.execute_in_uow(uow, child_task, now)
            assert child_result.run_id is not None
            request.dispatch(child_task.id, child_result.run_id, now)
            uow.delegation_requests.add(request)
            parent.wait_for_delegation(now, request.id)
            uow.runs.save(parent)
            removed = uow.work_queue.remove_if_claimed(
                parent.id, worker_id, claim_token, claim_generation, now
            )
            if not removed:
                raise ClaimLost(f"delegation dispatch lost claim for run {parent.id}")
            LifecycleEvents.append_run_events(
                uow,
                parent,
                now,
                [
                    (
                        RunEventType.DELEGATION_DISPATCHED,
                        {
                            "delegation_request_id": str(request.id),
                            "child_task_id": str(child_task.id),
                            "child_run_id": str(child_result.run_id),
                            "target_agent_id": str(agent.id),
                            "status": request.status.value,
                        },
                        None,
                    ),
                    (
                        RunEventType.RUN_WAITING_FOR_DELEGATION,
                        {
                            "run_id": str(parent.id),
                            "delegation_request_id": str(request.id),
                        },
                        None,
                    ),
                ],
            )
            uow.commit()
            return request

    @staticmethod
    def _require_claim(
        uow: UnitOfWork,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> None:
        if not uow.work_queue.is_claim_active(
            run_id, worker_id, claim_token, claim_generation, now
        ):
            raise ClaimLost(f"delegation dispatch requires an exact active claim for run {run_id}")

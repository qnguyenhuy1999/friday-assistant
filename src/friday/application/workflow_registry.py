"""Workflow registry control-plane use cases; never executes a node."""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

from friday.application.errors import EntityConflict, WorkflowNotFound, WorkflowRevisionNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    AgentId,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeId,
    WorkflowId,
    WorkflowNode,
    WorkflowNodeId,
    WorkflowRevision,
    WorkflowRevisionId,
    WorkflowRevisionSourceKind,
)
from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.json_value import JsonValue


class WorkflowNodeInput(TypedDict):
    node_key: str
    target_agent_id: str
    objective: str
    input_payload: JsonValue
    expected_output_contract: str


class WorkflowEdgeInput(TypedDict):
    from_node: str
    to_node: str


class CreateWorkflow:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, key: str, display_name: str, description: str) -> Workflow:
        workflow = Workflow.new(
            id=WorkflowId.new(),
            key=key,
            display_name=display_name,
            description=description,
            created_at=self._clock.now(),
        )
        with self._uow_factory() as uow:
            uow.workflows.add(workflow)
            uow.commit()
        return workflow


class CreateWorkflowRevision:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        *,
        workflow_id: WorkflowId,
        nodes: list[WorkflowNodeInput],
        edges: list[WorkflowEdgeInput],
        source_kind: WorkflowRevisionSourceKind,
    ) -> WorkflowRevision:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFound(workflow_id)
            if workflow.status.value == "archived":
                raise EntityConflict("archived workflow cannot receive revisions")
            revision_id = WorkflowRevisionId.new()
            by_key: dict[str, WorkflowNode] = {}
            node_values: list[WorkflowNode] = []
            for item in nodes:
                agent_id = AgentId.parse(item["target_agent_id"])
                if uow.agents.get(agent_id) is None:
                    raise EntityConflict("workflow target agent not found")
                node = WorkflowNode(
                    id=WorkflowNodeId.new(),
                    revision_id=revision_id,
                    node_key=item["node_key"],
                    target_agent_id=agent_id,
                    objective=item["objective"],
                    input_payload=item["input_payload"],
                    expected_output_contract=item["expected_output_contract"],
                    created_at=self._clock.now(),
                )
                node_values.append(node)
                by_key[node.node_key] = node
            for edge_item in edges:
                if edge_item["from_node"] not in by_key or edge_item["to_node"] not in by_key:
                    raise DomainValidationError("WorkflowEdge endpoint does not belong to revision")
            edge_values = [
                WorkflowEdge(
                    id=WorkflowEdgeId.new(),
                    revision_id=revision_id,
                    from_node_id=by_key[edge_item["from_node"]].id,
                    to_node_id=by_key[edge_item["to_node"]].id,
                    created_at=self._clock.now(),
                )
                for edge_item in edges
            ]
            revision = WorkflowRevision.new(
                id=revision_id,
                workflow_id=workflow_id,
                version=uow.workflow_revisions.next_version(workflow_id),
                nodes=node_values,
                edges=edge_values,
                source_kind=source_kind,
                created_at=self._clock.now(),
            )
            uow.workflow_revisions.add(revision)
            uow.commit()
            return revision


class ActivateWorkflowRevision:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, workflow_id: WorkflowId, revision_id: WorkflowRevisionId) -> Workflow:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFound(workflow_id)
            revision = uow.workflow_revisions.get(revision_id)
            if revision is None:
                raise WorkflowRevisionNotFound(revision_id)
            try:
                workflow.activate(revision, self._clock.now())
            except InvalidStateTransition as exc:
                raise EntityConflict(str(exc)) from exc
            uow.workflows.save(workflow)
            uow.commit()
            return workflow


class _WorkflowLifecycle:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def _change(self, workflow_id: WorkflowId, method: str) -> Workflow:
        with self._uow_factory() as uow:
            workflow = uow.workflows.get(workflow_id)
            if workflow is None:
                raise WorkflowNotFound(workflow_id)
            try:
                getattr(workflow, method)(self._clock.now())
            except InvalidStateTransition as exc:
                raise EntityConflict(str(exc)) from exc
            uow.workflows.save(workflow)
            uow.commit()
            return workflow


class DisableWorkflow(_WorkflowLifecycle):
    def execute(self, workflow_id: WorkflowId) -> Workflow:
        return self._change(workflow_id, "disable")


class ArchiveWorkflow(_WorkflowLifecycle):
    def execute(self, workflow_id: WorkflowId) -> Workflow:
        return self._change(workflow_id, "archive")


class GetWorkflow:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, workflow_id: WorkflowId) -> Workflow:
        with self._uow_factory() as uow:
            value = uow.workflows.get(workflow_id)
            if value is None:
                raise WorkflowNotFound(workflow_id)
            return value

    def list_revisions(self, workflow_id: WorkflowId) -> list[WorkflowRevision]:
        with self._uow_factory() as uow:
            if uow.workflows.get(workflow_id) is None:
                raise WorkflowNotFound(workflow_id)
            return uow.workflow_revisions.list_for_workflow(workflow_id)


class ListWorkflows:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, limit: int = 100) -> list[Workflow]:
        with self._uow_factory() as uow:
            return uow.workflows.list(limit)

    def page(
        self, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Workflow]:
        with self._uow_factory() as uow:
            return uow.workflows.list_page(limit, after_created_at, after_id)

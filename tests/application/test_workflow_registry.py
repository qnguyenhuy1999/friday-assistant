from __future__ import annotations

import pytest

from friday.application.agent_registry import CreateAgent
from friday.application.errors import (
    EntityConflict,
    WorkflowNotFound,
    WorkflowRevisionNotFound,
)
from friday.application.workflow_registry import (
    ActivateWorkflowRevision,
    ArchiveWorkflow,
    CreateWorkflow,
    CreateWorkflowRevision,
    DisableWorkflow,
    GetWorkflow,
    ListWorkflows,
    WorkflowEdgeInput,
    WorkflowNodeInput,
)
from friday.domain import AgentId, WorkflowId, WorkflowRevisionId, WorkflowRevisionSourceKind
from friday.domain.errors import DomainValidationError
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def _revision_inputs(agent_id: AgentId) -> tuple[list[WorkflowNodeInput], list[WorkflowEdgeInput]]:
    nodes: list[WorkflowNodeInput] = [
        {
            "node_key": "start",
            "target_agent_id": str(agent_id),
            "objective": "start",
            "input_payload": {},
            "expected_output_contract": "done",
        },
        {
            "node_key": "finish",
            "target_agent_id": str(agent_id),
            "objective": "finish",
            "input_payload": {},
            "expected_output_contract": "done",
        },
    ]
    edges: list[WorkflowEdgeInput] = [{"from_node": "start", "to_node": "finish"}]
    return nodes, edges


def test_workflow_revision_lifecycle_and_keyset_page() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    agent = CreateAgent(factory, clock).execute(
        key="workflow.target", display_name="Target", description=""
    )
    workflow = CreateWorkflow(factory, clock).execute(
        key="workflow.registry", display_name="Registry", description=""
    )
    second = CreateWorkflow(factory, clock).execute(
        key="workflow.second", display_name="Second", description=""
    )
    nodes, edges = _revision_inputs(agent.id)
    revision = CreateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id,
        nodes=nodes,
        edges=edges,
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
    )

    assert {value.id for value in ListWorkflows(factory).execute(10)} == {workflow.id, second.id}
    ordered = sorted((workflow, second), key=lambda value: (value.created_at, str(value.id)))
    first_page = ListWorkflows(factory).page(1, None, None)
    assert first_page == [ordered[0]]
    after_page = ListWorkflows(factory).page(1, first_page[0].created_at, str(first_page[0].id))
    assert after_page == [ordered[1]]
    assert GetWorkflow(factory).execute(workflow.id) is workflow
    assert GetWorkflow(factory).list_revisions(workflow.id) == [revision]

    activated = ActivateWorkflowRevision(factory, clock).execute(
        workflow_id=workflow.id, revision_id=revision.id
    )
    assert activated.active_revision_id == revision.id
    assert DisableWorkflow(factory, clock).execute(workflow.id).status.value == "disabled"
    assert ArchiveWorkflow(factory, clock).execute(workflow.id).status.value == "archived"

    with pytest.raises(EntityConflict, match="archived"):
        ActivateWorkflowRevision(factory, clock).execute(
            workflow_id=workflow.id, revision_id=revision.id
        )
    with pytest.raises(EntityConflict, match="archived"):
        CreateWorkflowRevision(factory, clock).execute(
            workflow_id=workflow.id,
            nodes=nodes,
            edges=edges,
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
        )
    with pytest.raises(EntityConflict, match="archived"):
        DisableWorkflow(factory, clock).execute(workflow.id)


def test_workflow_registry_rejects_missing_and_invalid_inputs() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    workflow = CreateWorkflow(factory, clock).execute(
        key="workflow.registry", display_name="Registry", description=""
    )
    agent = CreateAgent(factory, clock).execute(
        key="workflow.target", display_name="Target", description=""
    )
    nodes, edges = _revision_inputs(agent.id)

    with pytest.raises(WorkflowNotFound):
        CreateWorkflowRevision(factory, clock).execute(
            workflow_id=WorkflowId.new(),
            nodes=nodes,
            edges=edges,
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
        )
    invalid_node: WorkflowNodeInput = {
        "node_key": nodes[0]["node_key"],
        "target_agent_id": str(AgentId.new()),
        "objective": nodes[0]["objective"],
        "input_payload": nodes[0]["input_payload"],
        "expected_output_contract": nodes[0]["expected_output_contract"],
    }
    with pytest.raises(EntityConflict, match="target agent"):
        CreateWorkflowRevision(factory, clock).execute(
            workflow_id=workflow.id,
            nodes=[invalid_node],
            edges=[],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
        )
    with pytest.raises(DomainValidationError, match="endpoint"):
        CreateWorkflowRevision(factory, clock).execute(
            workflow_id=workflow.id,
            nodes=nodes,
            edges=[{"from_node": "start", "to_node": "missing"}],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
        )

    with pytest.raises(WorkflowNotFound):
        GetWorkflow(factory).execute(WorkflowId.new())
    with pytest.raises(WorkflowNotFound):
        GetWorkflow(factory).list_revisions(WorkflowId.new())
    with pytest.raises(WorkflowNotFound):
        DisableWorkflow(factory, clock).execute(WorkflowId.new())
    with pytest.raises(WorkflowNotFound):
        ActivateWorkflowRevision(factory, clock).execute(
            workflow_id=WorkflowId.new(), revision_id=WorkflowRevisionId.new()
        )
    with pytest.raises(WorkflowRevisionNotFound):
        ActivateWorkflowRevision(factory, clock).execute(
            workflow_id=workflow.id, revision_id=WorkflowRevisionId.new()
        )

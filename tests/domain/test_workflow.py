from datetime import UTC, datetime

import pytest

import friday.domain.workflow as workflow_module
from friday.domain import (
    AgentId,
    DomainValidationError,
    InvalidStateTransition,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeId,
    WorkflowId,
    WorkflowNode,
    WorkflowNodeId,
    WorkflowRevision,
    WorkflowRevisionId,
    WorkflowRevisionSourceKind,
    WorkflowStatus,
)
from friday.domain.json_value import JsonValue
from friday.domain.workflow import (
    MAX_WORKFLOW_INPUT_DEPTH,
    MAX_WORKFLOW_NODES,
    validate_workflow_dag,
    validate_workflow_key,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)

AGENT = AgentId.parse("00000000-0000-0000-0000-000000000001")


def _graph(order: tuple[str, ...] = ("a", "b")) -> WorkflowRevision:
    revision_id = WorkflowRevisionId.new()
    nodes = [
        WorkflowNode(WorkflowNodeId.new(), revision_id, key, AGENT, key, {}, "done", T0)
        for key in order
    ]
    edges = [WorkflowEdge(WorkflowEdgeId.new(), revision_id, nodes[0].id, nodes[1].id, T0)]
    return WorkflowRevision.new(
        id=revision_id,
        workflow_id=WorkflowId.new(),
        version=1,
        nodes=nodes,
        edges=edges,
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=T0,
    )


def test_canonical_hash_is_independent_of_node_order() -> None:
    first = _graph()
    revision_id = WorkflowRevisionId.new()
    nodes = [
        WorkflowNode(WorkflowNodeId.new(), revision_id, key, AGENT, key, {}, "done", T0)
        for key in ("b", "a")
    ]
    edges = [WorkflowEdge(WorkflowEdgeId.new(), revision_id, nodes[1].id, nodes[0].id, T0)]
    second = WorkflowRevision.new(
        id=revision_id,
        workflow_id=first.workflow_id,
        version=1,
        nodes=nodes,
        edges=edges,
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=T0,
    )
    assert first.content_sha256 == second.content_sha256


def test_revision_rejects_foreign_node_ownership_before_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_id = WorkflowRevisionId.new()
    foreign = WorkflowRevisionId.new()
    node = WorkflowNode(WorkflowNodeId.new(), foreign, "a", AgentId.new(), "do", {}, "done", T0)
    canonicalized = False

    def canonicalize(*args: object, **kwargs: object) -> str:
        nonlocal canonicalized
        canonicalized = True
        return "should not be hashed"

    monkeypatch.setattr(workflow_module, "canonical_workflow_content", canonicalize)
    with pytest.raises(DomainValidationError, match="ownership"):
        WorkflowRevision.new(
            id=revision_id,
            workflow_id=WorkflowId.new(),
            version=1,
            nodes=[node],
            edges=[],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
            created_at=T0,
        )
    assert not canonicalized


def test_revision_rejects_foreign_edge_ownership_before_hash() -> None:
    revision_id = WorkflowRevisionId.new()
    foreign = WorkflowRevisionId.new()
    nodes = [
        WorkflowNode(WorkflowNodeId.new(), revision_id, "a", AgentId.new(), "do", {}, "done", T0),
        WorkflowNode(WorkflowNodeId.new(), revision_id, "b", AgentId.new(), "do", {}, "done", T0),
    ]
    edge = WorkflowEdge(WorkflowEdgeId.new(), foreign, nodes[0].id, nodes[1].id, T0)
    with pytest.raises(DomainValidationError, match="ownership"):
        WorkflowRevision.new(
            id=revision_id,
            workflow_id=WorkflowId.new(),
            version=1,
            nodes=nodes,
            edges=[edge],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
            created_at=T0,
        )


def test_revision_rejects_unknown_edge_endpoint_without_key_error() -> None:
    revision_id = WorkflowRevisionId.new()
    node = WorkflowNode(WorkflowNodeId.new(), revision_id, "a", AgentId.new(), "do", {}, "done", T0)
    edge = WorkflowEdge(WorkflowEdgeId.new(), revision_id, node.id, WorkflowNodeId.new(), T0)
    with pytest.raises(DomainValidationError, match="endpoint"):
        WorkflowRevision.new(
            id=revision_id,
            workflow_id=WorkflowId.new(),
            version=1,
            nodes=[node],
            edges=[edge],
            source_kind=WorkflowRevisionSourceKind.OPERATOR,
            created_at=T0,
        )


def test_validate_workflow_key_rejects_invalid_shapes() -> None:
    with pytest.raises(DomainValidationError):
        validate_workflow_key("")
    with pytest.raises(DomainValidationError):
        validate_workflow_key("Not-Lowercase")
    with pytest.raises(DomainValidationError):
        validate_workflow_key("x" * 129)


def test_validate_workflow_dag_rejects_empty_too_large_and_cyclic_graphs() -> None:
    revision_id = WorkflowRevisionId.new()
    with pytest.raises(DomainValidationError, match="at least one node"):
        validate_workflow_dag([], [])

    too_many = [
        WorkflowNode(
            WorkflowNodeId.new(), revision_id, f"n{i}", AgentId.new(), "do", {}, "done", T0
        )
        for i in range(MAX_WORKFLOW_NODES + 1)
    ]
    with pytest.raises(DomainValidationError, match="exceeds limits"):
        validate_workflow_dag(too_many, [])

    a = WorkflowNode(WorkflowNodeId.new(), revision_id, "a", AgentId.new(), "do", {}, "done", T0)
    b = WorkflowNode(WorkflowNodeId.new(), revision_id, "b", AgentId.new(), "do", {}, "done", T0)
    duplicate_key = WorkflowNode(
        WorkflowNodeId.new(), revision_id, "a", AgentId.new(), "do", {}, "done", T0
    )
    with pytest.raises(DomainValidationError, match="duplicate WorkflowNode.node_key"):
        validate_workflow_dag([a, duplicate_key], [])

    cycle = [
        WorkflowEdge(WorkflowEdgeId.new(), revision_id, a.id, b.id, T0),
        WorkflowEdge(WorkflowEdgeId.new(), revision_id, b.id, a.id, T0),
    ]
    with pytest.raises(DomainValidationError, match="cycle"):
        validate_workflow_dag([a, b], cycle)


def test_workflow_node_rejects_input_payload_exceeding_depth() -> None:
    nested: JsonValue = True
    for _ in range(MAX_WORKFLOW_INPUT_DEPTH + 1):
        nested = {"child": nested}
    with pytest.raises(DomainValidationError, match="depth"):
        WorkflowNode(
            WorkflowNodeId.new(),
            WorkflowRevisionId.new(),
            "a",
            AgentId.new(),
            "do",
            nested,
            "done",
            T0,
        )


def test_workflow_edge_rejects_self_edge() -> None:
    node_id = WorkflowNodeId.new()
    with pytest.raises(DomainValidationError, match="self-edge"):
        WorkflowEdge(WorkflowEdgeId.new(), WorkflowRevisionId.new(), node_id, node_id, T0)


def test_workflow_lifecycle_rejects_invalid_shapes_and_transitions() -> None:
    with pytest.raises(DomainValidationError):
        Workflow.new(
            id=WorkflowId.new(), key="a", display_name="   ", description="", created_at=T0
        )

    workflow = Workflow.new(
        id=WorkflowId.new(), key="a.b", display_name="A", description="", created_at=T0
    )
    unrelated_revision_id = WorkflowRevisionId.new()
    unrelated_revision = WorkflowRevision.new(
        id=unrelated_revision_id,
        workflow_id=WorkflowId.new(),
        version=1,
        nodes=[
            WorkflowNode(
                WorkflowNodeId.new(),
                unrelated_revision_id,
                "a",
                AgentId.new(),
                "do",
                {},
                "done",
                T0,
            )
        ],
        edges=[],
        source_kind=WorkflowRevisionSourceKind.OPERATOR,
        created_at=T0,
    )
    with pytest.raises(DomainValidationError, match="does not belong"):
        workflow.activate(unrelated_revision, T0)

    workflow.archive(T0)
    assert workflow.status is WorkflowStatus.ARCHIVED
    workflow.archive(T0)  # archiving an already-archived Workflow is a no-op
    assert workflow.status is WorkflowStatus.ARCHIVED
    with pytest.raises(InvalidStateTransition):
        workflow.disable(T0)

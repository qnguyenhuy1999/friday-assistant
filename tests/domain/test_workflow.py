from datetime import UTC, datetime

import pytest

from friday.domain import (
    AgentId,
    DomainValidationError,
    WorkflowEdge,
    WorkflowEdgeId,
    WorkflowId,
    WorkflowNode,
    WorkflowNodeId,
    WorkflowRevision,
    WorkflowRevisionId,
    WorkflowRevisionSourceKind,
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


def test_revision_rejects_foreign_node_ownership_before_hash() -> None:
    revision_id = WorkflowRevisionId.new()
    foreign = WorkflowRevisionId.new()
    node = WorkflowNode(WorkflowNodeId.new(), foreign, "a", AgentId.new(), "do", {}, "done", T0)
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

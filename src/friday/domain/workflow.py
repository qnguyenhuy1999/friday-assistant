"""Immutable, definition-only Workflow registry and DAG contract.

Workflow data describes logical Agent work.  It is deliberately free of
runtime, tool, approval, credential, and execution fields; authority remains
owned by Friday and execution is a later phase.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import (
    AgentId,
    WorkflowEdgeId,
    WorkflowId,
    WorkflowNodeId,
    WorkflowRevisionId,
)
from friday.domain.json_value import JsonValue, ensure_json_value
from friday.domain.time import ensure_utc

MAX_WORKFLOW_NODES = 64
MAX_WORKFLOW_EDGES = 256
MAX_WORKFLOW_KEY_LENGTH = 128
MAX_WORKFLOW_OBJECTIVE_LENGTH = 4000
MAX_WORKFLOW_OUTPUT_LENGTH = 4000
MAX_WORKFLOW_INPUT_BYTES = 16_384
MAX_WORKFLOW_INPUT_DEPTH = 8
MAX_WORKFLOW_INPUT_NODES = 256
_KEY = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


class WorkflowStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class WorkflowRevisionSourceKind(StrEnum):
    OPERATOR = "operator"
    IMPORTED = "imported"


def _check_text(value: str, name: str, maximum: int) -> str:
    if not value or len(value) > maximum:
        raise DomainValidationError(f"{name} must be non-empty and within the maximum length")
    return value


def validate_workflow_key(value: str) -> str:
    if not value or len(value) > MAX_WORKFLOW_KEY_LENGTH or not _KEY.fullmatch(value):
        raise DomainValidationError("Workflow.key must be a stable machine-readable identifier")
    return value


def _json_stats(value: object, depth: int = 0) -> int:
    if depth > MAX_WORKFLOW_INPUT_DEPTH:
        raise DomainValidationError("WorkflowNode.input_payload exceeds maximum depth")
    if isinstance(value, dict):
        return 1 + sum(_json_stats(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(_json_stats(v, depth + 1) for v in value)
    return 1


def validate_workflow_input(value: object) -> JsonValue:
    result = ensure_json_value(value, path="WorkflowNode.input_payload")
    if (
        len(
            json.dumps(
                result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        )
        > MAX_WORKFLOW_INPUT_BYTES
    ):
        raise DomainValidationError("WorkflowNode.input_payload exceeds maximum bytes")
    if _json_stats(result) > MAX_WORKFLOW_INPUT_NODES:
        raise DomainValidationError("WorkflowNode.input_payload exceeds maximum nodes")
    return result


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: WorkflowNodeId
    revision_id: WorkflowRevisionId
    node_key: str
    target_agent_id: AgentId
    objective: str
    input_payload: JsonValue
    expected_output_contract: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.node_key or len(self.node_key) > 128 or not _KEY.fullmatch(self.node_key):
            raise DomainValidationError("WorkflowNode.node_key is invalid")
        _check_text(self.objective, "WorkflowNode.objective", MAX_WORKFLOW_OBJECTIVE_LENGTH)
        _check_text(
            self.expected_output_contract,
            "WorkflowNode.expected_output_contract",
            MAX_WORKFLOW_OUTPUT_LENGTH,
        )
        validate_workflow_input(self.input_payload)
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    id: WorkflowEdgeId
    revision_id: WorkflowRevisionId
    from_node_id: WorkflowNodeId
    to_node_id: WorkflowNodeId
    created_at: datetime

    def __post_init__(self) -> None:
        if self.from_node_id == self.to_node_id:
            raise DomainValidationError("WorkflowEdge cannot be a self-edge")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))


def canonical_workflow_content(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> str:
    by_id = {n.id: n.node_key for n in nodes}
    payload = {
        "nodes": [
            {
                "node_key": n.node_key,
                "target_agent_id": str(n.target_agent_id),
                "objective": n.objective,
                "input_payload": n.input_payload,
                "expected_output_contract": n.expected_output_contract,
            }
            for n in sorted(nodes, key=lambda n: n.node_key)
        ],
        "edges": [
            {"from": by_id[e.from_node_id], "to": by_id[e.to_node_id]}
            for e in sorted(edges, key=lambda e: (by_id[e.from_node_id], by_id[e.to_node_id]))
        ],
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def validate_workflow_dag(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> None:
    if not nodes:
        raise DomainValidationError("WorkflowRevision requires at least one node")
    if len(nodes) > MAX_WORKFLOW_NODES or len(edges) > MAX_WORKFLOW_EDGES:
        raise DomainValidationError("WorkflowRevision graph exceeds limits")
    keys = [n.node_key for n in nodes]
    if len(set(keys)) != len(keys):
        raise DomainValidationError("duplicate WorkflowNode.node_key")
    ids = {n.id for n in nodes}
    pairs: set[tuple[WorkflowNodeId, WorkflowNodeId]] = set()
    indegree = {n.id: 0 for n in nodes}
    outgoing: dict[WorkflowNodeId, list[WorkflowNodeId]] = {n.id: [] for n in nodes}
    for e in edges:
        if (
            e.revision_id != nodes[0].revision_id
            or e.from_node_id not in ids
            or e.to_node_id not in ids
        ):
            raise DomainValidationError("WorkflowEdge endpoint does not belong to revision")
        pair = (e.from_node_id, e.to_node_id)
        if pair in pairs:
            raise DomainValidationError("duplicate WorkflowEdge")
        pairs.add(pair)
        indegree[e.to_node_id] += 1
        outgoing[e.from_node_id].append(e.to_node_id)
    queue = sorted([x for x, d in indegree.items() if d == 0], key=str)
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for target in sorted(outgoing[current], key=str):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort(key=str)
    if visited != len(nodes):
        raise DomainValidationError("WorkflowRevision graph contains a cycle")


@dataclass(frozen=True, slots=True)
class WorkflowRevision:
    id: WorkflowRevisionId
    workflow_id: WorkflowId
    version: int
    content_sha256: str
    source_kind: WorkflowRevisionSourceKind
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.version < 1:
            raise DomainValidationError("WorkflowRevision.version must be positive")
        validate_workflow_dag(list(self.nodes), list(self.edges))
        expected = hashlib.sha256(
            canonical_workflow_content(list(self.nodes), list(self.edges)).encode()
        ).hexdigest()
        if self.content_sha256 != expected:
            raise DomainValidationError("workflow_integrity_failed")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise DomainValidationError("WorkflowRevision.content_sha256 must be lowercase sha256")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))

    @classmethod
    def new(
        cls,
        *,
        id: WorkflowRevisionId,
        workflow_id: WorkflowId,
        version: int,
        nodes: list[WorkflowNode],
        edges: list[WorkflowEdge],
        source_kind: WorkflowRevisionSourceKind,
        created_at: datetime,
    ) -> WorkflowRevision:
        content = canonical_workflow_content(nodes, edges)
        return cls(
            id,
            workflow_id,
            version,
            hashlib.sha256(content.encode()).hexdigest(),
            source_kind,
            tuple(nodes),
            tuple(edges),
            created_at,
        )


@dataclass(slots=True)
class Workflow:
    _id: WorkflowId
    _key: str
    _display_name: str
    _description: str
    _status: WorkflowStatus
    _active_revision_id: WorkflowRevisionId | None
    _created_at: datetime
    _updated_at: datetime

    @classmethod
    def new(
        cls, *, id: WorkflowId, key: str, display_name: str, description: str, created_at: datetime
    ) -> Workflow:
        if not display_name.strip():
            raise DomainValidationError("Workflow.display_name must not be empty")
        now = ensure_utc(created_at)
        return cls(
            id,
            validate_workflow_key(key),
            display_name.strip(),
            description.strip(),
            WorkflowStatus.ACTIVE,
            None,
            now,
            now,
        )

    id = property(lambda s: s._id)
    key = property(lambda s: s._key)
    display_name = property(lambda s: s._display_name)
    description = property(lambda s: s._description)
    status = property(lambda s: s._status)
    active_revision_id = property(lambda s: s._active_revision_id)
    created_at = property(lambda s: s._created_at)
    updated_at = property(lambda s: s._updated_at)

    def activate(self, revision: WorkflowRevision, at: datetime) -> None:
        if self.status is WorkflowStatus.ARCHIVED:
            raise InvalidStateTransition("Workflow", self.status.value, "activate")
        if revision.workflow_id != self.id:
            raise DomainValidationError("Workflow revision does not belong to workflow")
        self._active_revision_id, self._status, self._updated_at = (
            revision.id,
            WorkflowStatus.ACTIVE,
            ensure_utc(at),
        )

    def disable(self, at: datetime) -> None:
        if self.status is WorkflowStatus.ARCHIVED:
            raise InvalidStateTransition("Workflow", self.status.value, "disabled")
        self._status, self._updated_at = WorkflowStatus.DISABLED, ensure_utc(at)

    def archive(self, at: datetime) -> None:
        if self.status is not WorkflowStatus.ARCHIVED:
            self._status, self._updated_at = WorkflowStatus.ARCHIVED, ensure_utc(at)

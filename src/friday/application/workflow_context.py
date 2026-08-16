"""Canonical Workflow node context construction.

Both the Workflow scheduler (pre-dispatch validation) and the
AgentRunProcessor (brain context) build a node's predecessor context through
this single deterministic builder, so a child Run and its scheduler agree on
exactly when a node's durable context is legal.

Nothing is truncated: if any predecessor result or the aggregate node context
cannot fit its durable bound, the builder raises ``WorkflowNodeContextTooLarge``
and the scheduler must fail the dependent node closed (``BLOCKED``) before any
child Task or Run is created.  Truncation would silently corrupt the JSON a
brain must reason over; failing closed keeps scheduling and execution in
agreement.
"""

from __future__ import annotations

import json

from friday.application.ports import UnitOfWork
from friday.domain.workflow_execution import (
    WorkflowNodeExecution,
    WorkflowNodeExecutionStatus,
)

MAX_WORKFLOW_CONTEXT_CHARS = 6000
MAX_WORKFLOW_PREDECESSOR_RESULT_CHARS = 2000


class WorkflowNodeContextTooLarge(ValueError):
    """The complete, deterministic Workflow node context exceeds its bound."""


def build_workflow_node_context(uow: UnitOfWork, node_execution: WorkflowNodeExecution) -> str:
    """Render the complete deterministic context for one node's child Run.

    Raises ``WorkflowNodeContextTooLarge`` when a predecessor result or the
    aggregate context exceeds the durable bound.  Raises ``ValueError`` for an
    inconsistent/missing frozen execution shape.  Never truncates.
    """
    execution = uow.workflow_executions.get(node_execution.workflow_execution_id)
    if execution is None:
        raise ValueError("workflow_execution_context_missing")
    revision = uow.workflow_revisions.get(execution.workflow_revision_id)
    workflow = uow.workflows.get(execution.workflow_id)
    if revision is None or workflow is None or revision.workflow_id != workflow.id:
        raise ValueError("workflow_execution_context_invalid")
    definition = next(
        (node for node in revision.nodes if node.id == node_execution.workflow_node_id), None
    )
    if definition is None or definition.node_key != node_execution.node_key:
        raise ValueError("workflow_node_context_invalid")
    payload_json = json.dumps(definition.input_payload, sort_keys=True, separators=(",", ":"))
    lines = [
        "# WORKFLOW NODE",
        f"workflow_key: {workflow.key}",
        f"workflow_revision_version: {revision.version}",
        f"workflow_revision_sha256: {execution.workflow_content_sha256}",
        f"node_key: {definition.node_key}",
        f"objective: {definition.objective}",
        f"input_payload: {payload_json}",
        f"expected_output_contract: {definition.expected_output_contract}",
    ]
    predecessors = sorted(
        (edge.from_node_id for edge in revision.edges if edge.to_node_id == definition.id),
        key=lambda value: next(node.node_key for node in revision.nodes if node.id == value),
    )
    if predecessors:
        all_nodes = {
            item.workflow_node_id: item
            for item in uow.workflow_node_executions.list_by_execution(execution.id)
        }
        lines.append("# WORKFLOW PREDECESSORS")
        for predecessor_id in predecessors:
            predecessor = all_nodes.get(predecessor_id)
            if (
                predecessor is None
                or predecessor.status is not WorkflowNodeExecutionStatus.SUCCEEDED
                or predecessor.result_payload is None
            ):
                raise ValueError("workflow_predecessor_context_unavailable")
            predecessor_definition = next(
                node for node in revision.nodes if node.id == predecessor_id
            )
            result = json.dumps(
                predecessor.result_payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            if len(result) > MAX_WORKFLOW_PREDECESSOR_RESULT_CHARS:
                raise WorkflowNodeContextTooLarge(
                    "a predecessor result exceeds the durable workflow context bound"
                )
            lines.append(f"- {predecessor_definition.node_key}: {result}")
    rendered = "\n".join(lines)
    if len(rendered) > MAX_WORKFLOW_CONTEXT_CHARS:
        raise WorkflowNodeContextTooLarge(
            "the aggregate workflow context exceeds the durable bound"
        )
    return rendered

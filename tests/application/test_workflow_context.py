from typing import cast

from friday.application.agent_run_processor import _workflow_context_for_run
from friday.application.workflow_execution_use_cases import (
    ReconcileWorkflowExecution,
)
from friday.domain.identifiers import RunId
from friday.domain.json_value import JsonValue
from tests.application.test_workflow_execution_use_cases import T1, _bootstrap


def test_fanin_context_contains_only_succeeded_predecessor_results() -> None:
    edges = [(0, 1), (0, 2), (1, 3), (2, 3)]
    uow, factory, clock, *_rest, execution = _bootstrap(edges=edges, agent_count=4)
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    root = next(n for n in nodes if n.node_key == "node-0")
    child = uow.runs.get(cast(RunId, root.child_run_id))
    assert child is not None
    child.start(T1)
    child.succeed(T1)
    uow.runs.save(child)
    ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    branches = [n for n in nodes if n.node_key in {"node-1", "node-2"}]
    for node in branches:
        payload = {"node": node.node_key, "secret": "branch-only"}
        node.succeed(T1, cast(JsonValue, payload))
    ReconcileWorkflowExecution(factory, clock).execute(execution.id)
    nodes = uow.workflow_node_executions.list_by_execution(execution.id)
    join = next(n for n in nodes if n.node_key == "node-3")
    assert join.child_run_id is not None
    context = _workflow_context_for_run(uow, join.child_run_id)
    assert context is not None
    assert "node-1" in context and "node-2" in context
    assert "branch-only" in context
    assert "node-0" not in context
    assert "root-authority" not in context

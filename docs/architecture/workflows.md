# Workflow registry and DAG execution contract

A Workflow is a durable orchestration definition. It has a globally unique,
immutable key and an explicit lifecycle (`active`, `disabled`, `archived`).
Workflow revisions are immutable, monotonically versioned snapshots. A
revision is published atomically with its complete node and edge set.

Nodes identify logical Agents by `target_agent_id`; they do not select an
Agent revision. Agent revision selection is performed exactly once, at Workflow
execution freeze time, and the chosen revision is recorded per node. Edges
express only the prerequisite relationship between nodes. The registry
validates bounded JSON, node/edge limits, ownership, duplicate keys/edges, and
cycles using deterministic topological validation.

The revision SHA-256 is computed from canonical logical content: sorted node
keys, sorted endpoint keys, sorted JSON keys, compact UTF-8 JSON, and no UUID
ordering. Loading a revision recomputes this digest and fails closed on
tampering.

Workflow definitions do not execute tools. Workflow definitions do not confer
authority. They contain no runtime, provider, credential, shell, filesystem,
MCP, browser, computer-use, messaging, approval, or tool permission fields.
Friday remains the authority owner; Agents reason and Friday orchestrates.

## Phase 21 — frozen execution and bounded delegation (complete)

Step 4 adds frozen execution on top of the immutable registry. A hierarchy
marker, `TaskWorkflowBinding`, `RunWorkflowResolution`, `WorkflowExecution`,
and `WorkflowNodeExecution` make a Task that is bound to a Workflow execute
as a DAG of ordinary Friday Tasks/Runs.

- **`TaskWorkflowBinding`** — one durable Task↔Workflow association. A Task
  has either a `TaskAgentBinding` or a `TaskWorkflowBinding`, never both.
- **`RunWorkflowResolution`** — freezes one queued/running root Run to *exactly
  one* Workflow revision (frozen content SHA-256), under the Run's exact active
  claim, via a claim-fenced conditional insert.
- **`WorkflowExecution`** — durable snapshot of one root Run's DAG run: frozen
  Workflow + revision + content digest, executing status, and per-node state.
- **`WorkflowNodeExecution`** — durable per-node state (pending/dispatched/
  succeeded/failed/cancelled/blocked) with the exact frozen target Agent
  revision, child Task/Run lineage, and predecessor result payload.
- **Exact Workflow freeze** — the execution is pinned to the active revision's
  canonical content digest at bootstrap; every later reconcile reloads and
  re-verifies the frozen revision before scheduling anything.
- **Exact Agent freeze** — before any execution, node, Task, or Run is created,
  every node's target Agent must be active and resolvable, its active revision
  must hash to its recorded digest, and its runtime must be registered. Any
  failure aborts the whole bootstrap with no durable execution state.
- **Fan-out / fan-in scheduling** — bootstrap dispatches every node with no
  predecessors; reconciliation reaches a durable fixed point, dispatching a
  node only once *all* of its predecessors are succeeded, and waiting for every
  fan-in predecessor before its join dispatches.
- **`BLOCKED` propagation** — a failed/cancelled predecessor blocks every
  downstream node to the fixed point, and a Workflow with terminal nodes (but
  no success path) fails normally. A node whose complete predecessor context
  cannot fit the durable bound is blocked with `workflow_context_too_large`
  *before* any child Task/Run is created — context is never truncated.
- **Durable predecessor context / results** — a predecessor's result payload is
  stored durably on its node execution. A single canonical builder (used by
  both the scheduler and the agent runtime) renders the complete deterministic
  predecessor context for a child Run, validating a per-result bound and an
  aggregate bound with no lossy truncation.
- **Retry semantics** — a child Run's automatic retry inherits the frozen Agent
  revision; reconciliation never resurrects a node past `dispatched`, and a
  manual retry of a Workflow-owned child execution is rejected.
- **Authority isolation** — node child Runs are ordinary Tasks/Runs with the
  same provenance-only bindings as any other child. A Workflow influences
  orchestration; a Workflow never grants authority. Node Runs retain
  independent Friday approval/tool authority, and the root Run owns neither.
- **Cancellation limitation** — cancellation of a Task or its root Run while a
  Workflow execution is running is rejected (`WorkflowCancelNotSupportedWhileActive`)
  so child Runs are never orphaned under a cancelled root. Workflow
  cancellation happens only through the scheduler's node terminal paths.
- **Concurrency** — every guarantee comes from the database (claim fencing,
  unique constraints, conditional transitions). Bootstrap and fan-in scheduling
  are independently concurrent and converge to exactly one execution, one node
  set, and one dispatch per node: the `dispatched` transition is strict, so the
  loser of a dispatch race fails closed and rolls back its orphan child instead
  of publishing a duplicate.

## Delegation composition and recovery

A Workflow node is an ordinary frozen Agent Run and may use the same bounded
delegation path as any other Run. The resulting hierarchy can combine Workflow
fan-out/fan-in with nested descendants; the Workflow does not become a source
of authority for its nodes or their children.

- **Bounds** — nesting is limited to `MAX_DELEGATION_DEPTH = 3`; a Run has at
  most `MAX_DELEGATIONS_PER_RUN = 4` direct materialized child requests; a
  delegation tree has at most `MAX_DELEGATIONS_PER_TREE = 16` materialized
  requests. Historical materialized requests continue to consume these logical
  slots; retry attempts do not create another request.
- **Lineage** — a `DelegationRequest` owns the child execution identity rather
  than a single physical Run attempt. Reconciliation selects the latest
  attempt, so a stale failure cannot settle a parent or a Workflow node while a
  retry is queued or running. Manual retry of delegation-owned execution is
  forbidden.
- **Restart safety** — delegation terminalization is committed with normal Run
  terminalization. Worker maintenance scans bounded durable running Workflow
  executions whose latest child is terminal and reconciles them, recovering the
  crash window after a child terminal commit but before the original worker
  callback without starving behind healthy long-running Workflows.
- **Result boundary** — child results cross only to their immediate parent as
  reasoning context. `delegation_result_safety.py` preserves ordinary
  provenance and useful result data while redacting structurally identified or
  actual authority-bearing values such as approval references, authorization
  fingerprints, claim tokens, ToolInvocation references, bindings, and
  credentials.

## Intentional limitations

Active Workflow cancellation remains unsupported through the Task/Run paths,
as described above. Delegated authority-value collection walks a bounded tree
and may issue multiple repository reads near the maximum size; this is a
non-blocking performance follow-up, not a correctness dependency.

# Workflow registry and DAG contract

A Workflow is a durable orchestration definition. It has a globally unique,
immutable key and an explicit lifecycle (`active`, `disabled`, `archived`).
Workflow revisions are immutable, monotonically versioned snapshots. A
revision is published atomically with its complete node and edge set.

Nodes identify logical Agents by `target_agent_id`; they do not select an
Agent revision. Agent revision resolution is a future execution concern.
Edges express only the prerequisite relationship between nodes. The registry
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

Workflow execution is not implemented in Step 3. No Task/Run binding,
scheduler, delegation materialization, parallel execution, joins, retries, or
node output interpolation exists yet.

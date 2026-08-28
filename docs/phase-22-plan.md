# Phase 22 — Agent & Workflow Operator Studio

Phase 1–21 completed Friday Agent OS's core architecture. Phase 22 is product
expansion: it exposes existing, Friday-owned control-plane capabilities to
operators without changing the authority model.

## Step 1 — Agent Registry Operator UI + navigation ✅ complete

Step 1 adds the Agent registry to the web control plane. Operators can list
and inspect Agents, create an Agent, inspect immutable revisions, create a
new revision, activate one exact revision, and use the supported disable and
archive lifecycle operations. It also exposes existing frozen Run Agent
resolution as read-only provenance.

The governing architecture is unchanged:

```text
Agent decides / reasons.
Friday orchestrates.
Friday owns authority.
```

Agent instructions and runtime configuration are reasoning/runtime input only;
they do not grant authority. All side effects remain on the existing Run →
AgentRunProcessor → proposed action → risk → ApprovalRequest (when required)
→ ToolInvocation → ToolGateway path.

Workflow authoring is not part of Step 1.

## Step 2 — Workflow Registry Operator UI + Structured DAG Revision Authoring

Step 2 exposes the existing Workflow registry and immutable DAG revision model
to operators. The web control plane supports Workflow listing and creation,
detail and revision provenance inspection, deterministic structured node/edge
drafting, local feedback for malformed JSON and obvious DAG errors, exact
revision activation, and the supported disable/archive lifecycle.

There is no graphical DAG canvas or drag-and-drop editor in Step 2. Task ↔
Workflow binding is intentionally deferred until the Workflow operator surface
has been reviewed; no binding UI is exposed here.

Workflow definitions influence orchestration only. They do not grant tool,
filesystem, shell, MCP, browser/computer, messaging, provider, approval, claim,
or execution-fencing authority. Revisions are immutable, version allocation is
server-owned, and Workflow execution freezes the exact revision and content
SHA used for that execution. A selected revision pointer is distinct from an
effectively active Workflow, so a disabled Workflow can activate its existing
selected revision without creating a new revision. Archived Workflows are
read-only while their metadata, revisions, DAGs, and provenance remain
inspectable.

Future Phase 22 steps remain TBD after Step 2 review.

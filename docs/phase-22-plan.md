# Phase 22 — Agent & Workflow Operator Studio

Phase 1–21 completed Friday Agent OS's core architecture. Phase 22 is product
expansion: it exposes existing, Friday-owned control-plane capabilities to
operators without changing the authority model.

## Step 1 — Agent Registry Operator UI + navigation

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

Workflow authoring is not part of Step 1. Future Phase 22 steps: TBD after
Step 1 review.

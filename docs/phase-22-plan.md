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

## Step 2 — Workflow Registry Operator UI + Structured DAG Revision Authoring ✅ complete

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

Operator collection loading is bounded. The Agent registry uses cursor pages,
and the Workflow editor can load additional Agent pages rather than silently
stopping at the first 100 records. Workflow revision history is requested in
bounded newest-first pages keyed by immutable server-owned revision versions.
Target Agent status remains advisory in the authoring UI: disabled Agents or
Agents without a selected revision are visibly warned about because future
execution may fail to resolve them, but the UI does not invent a stronger
authority or eligibility rule than Friday's backend owns.

## Step 3 — Task Execution Target Binding & Launch Readiness

Step 3 adds the first-class Task Detail route
`?view=task&id=<task-id>`. From that surface, an operator can inspect a Task's
mutable execution-target configuration, bind or clear its existing Agent or
Workflow binding, review launch readiness, start an ordinary Run, and navigate
to its Schedules. The Tasks registry remains available and opens the exact
Task Detail page.

A Task has exactly one logical execution-target state: Default Friday runtime,
Agent, Workflow, or Inconsistent. Default Friday runtime means that both
`TaskAgentBinding` and `TaskWorkflowBinding` are absent; the UI does not
fabricate an Agent identity. Agent and Workflow bindings remain mutually
exclusive, and cross-kind changes are explicit: the current binding must be
cleared in one operation before the other kind can be bound. The UI does not
pretend that two non-atomic requests are one switch.

Agent binding eligibility remains the backend-owned rule: an Agent must be
`active` and have a selected revision. Disabled, archived, and unselected
Agents remain visible where practical with the reason they cannot be newly
bound. A bound disabled Agent or active Agent without a selected revision is a
repairable readiness warning; a bound archived Agent is terminal under the
supported lifecycle and cannot resolve future unresolved Runs. Archived
Workflows cannot be newly bound. A bound archived Workflow is likewise
terminal and cannot be reactivated through the supported lifecycle. Disabled
Workflows and Workflows without a selected revision may still be selected as
the backend allows, but the UI surfaces a repairable launch-readiness warning
rather than inventing a stronger client-side authority rule.

Launch readiness also respects Task lifecycle: only `pending` and `active`
Tasks can start another Run. Completed, failed, and cancelled Tasks remain
inspectable and their bindings remain explicitly manageable, but starting a
new Run is unavailable. Archived bound Agents and Workflows must be cleared or
replaced before a new Run can resolve safely.

Task binding is mutable future configuration, not frozen Run provenance.
Binding changes affect unresolved Runs, including queued Runs that have not
yet frozen their Agent or Workflow resolution. Once a worker publishes
`RunAgentResolution` or `RunWorkflowResolution`, that exact frozen provenance
is immutable and Task binding changes never rewrite it. Task Detail therefore
shows configuration while Run Detail shows frozen execution truth.

Starting a Run continues to use Friday's ordinary `tasks.startRun` path. The
browser queues the Run and never chooses Agent processing versus Workflow
orchestration; Friday's worker owns resolution, authority, risk assessment,
approval, ToolInvocation, and ToolGateway execution. The authority model
remains unchanged: Agent decides/reasons, Friday orchestrates, and Friday owns
authority. An observed state with both bindings present is treated as an
integrity error, fails closed, and is never repaired automatically.

Future Phase 22 work remains TBD after Step 3 review.

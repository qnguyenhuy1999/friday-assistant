# Phase 23 — Skill Operator Studio

## Step 1 — Skill Registry Operator UI + Immutable Revision Lifecycle & Frozen Run Provenance

Step 1 exposes Friday's existing Skill subsystem through a first-class
operator surface. The web control plane provides a paginated Skill registry,
exact Skill detail navigation, Skill creation, and inspection of lifecycle
metadata and immutable revision provenance.

The Skill lifecycle remains backend-owned. Skills can be active, disabled, or
archived; archived Skills are terminal and read-only in the operator surface.
The selected revision pointer is distinct from lifecycle readiness. Disabling a
Skill retains its selected pointer but does not make the Skill runtime
resolvable, and activating a newer permitted revision does not re-enable a
disabled Skill. There is no restore or enable operation in this Step.

Operators can create immutable operator revisions containing exact persisted
instructions. Friday owns revision IDs, monotonically increasing versions,
content SHA-256 values, source provenance, and timestamps. Creating a revision
does not select or activate it. Generated revisions remain promotion-controlled
and historical revisions require the existing approved rollback mechanism; the
operator surface exposes neither generated authoring nor rollback approval.

Direct activation is offered only for a strictly newer non-generated revision
when the Skill is not archived. The server remains authoritative for every
lifecycle and activation decision.

Run Detail includes read-only frozen Skill provenance from the durable Run
resolution: resolved status and timestamp, persisted binding order, Skill and
revision identifiers, version, source kind, content SHA-256, and exact frozen
instructions. The UI distinguishes unresolved Skill resolution from a resolved
Run with zero Skills. It never replaces frozen Run provenance with a Skill's
current selected revision.

Skill context continues to influence reasoning only. It grants no filesystem,
shell/process, MCP, browser/computer, messaging, provider, tool, approval,
claim, scheduling, retry, or execution-fencing authority. The protected
Run → AgentRunProcessor → reasoning → proposed action → risk → approval (when
required) → ToolInvocation → ToolGateway path is unchanged.

The Skill collection uses bounded opaque keyset pagination ordered by
`(created_at, id)` ascending. A no-parameter collection request retains the
legacy default of up to 100 Skills, while the operator registry explicitly
requests 25-item pages. Revision history supports bounded newest-first loading
with `before_version`, while the legacy unpaged revision endpoint remains
compatible. The browser uses the TypeScript SDK for both collection and
revision reads. Activation eligibility verifies the selected immutable revision
through an exact revision lookup and does not require loading historical pages.

## Step 2 — Task Skill Composition & Skill-Aware Launch Readiness

Step 2 extends Task Detail with an ordered Skill composition editor backed by
the existing atomic `PUT /v1/tasks/{task_id}/skills` operation. The browser
keeps add, remove, reorder, and clear operations in a local draft until the
operator explicitly saves the complete ordered list. Save sends exactly one
replacement request; discard restores the canonical persisted binding. The
server remains authoritative, enforces unique Skill IDs, and caps a Task at
16 Skills.

New bindings are selectable only when the current Skill is active and has a
selected revision. Disabled, archived, active-without-a-selected-revision,
and duplicate candidates remain visible with their lifecycle or eligibility
reason. Registry discovery remains paginated and can load later pages.

Task Detail reads every persisted bound Skill through an exact
`GET /v1/skills/{skill_id}` lookup. The bounded maximum of 16 bindings keeps
this fan-out bounded and avoids requiring registry pages to contain current
Task bindings. Missing or failed exact metadata verification fails launch
readiness closed. Disabled, archived, and active Skills without a selected
revision remain visible and explicitly repairable by removing or replacing
the binding; the UI does not auto-repair them and does not invent enable or
restore lifecycle actions.

Centralized launch readiness now accounts for Task Skill binding/detail
loading and failures, deterministically unresolvable persisted Skills, Skill
composition mutation pending, and unsaved local drafts. Start Run is disabled
until persisted Skill configuration is verifiable and the displayed draft is
either saved or discarded. Existing Agent/Workflow target rules and their
mutual-exclusion integrity error remains unchanged and blocks launch only;
it does not disable an otherwise-available Skill editor. Skill inspection,
add, remove, reorder, clear, save, and discard remain governed by the Task
Skill binding state. Skills coexist with the default runtime, Agent target, or
Workflow target and are never an execution target themselves.

The Task Detail route gives the editor an explicit React identity keyed by the
exact Task ID. Changing from one Task route to another remounts the editor and
resets its selected candidate, draft composition, and persisted-binding
synchronization state, so an unsaved draft can never cross Task boundaries.

Task Skill composition is mutable future configuration. It can affect an
unresolved queued Run until the worker resolves it. Worker resolution freezes
the Run's Skill IDs, selected revision IDs, order, instructions, hashes, and
provenance; later Task or Skill changes do not rewrite frozen Run Detail
history. Task Detail's execution preview is explicitly current configuration,
not frozen provenance.

Skills continue to influence reasoning only. They grant no filesystem,
shell/process, MCP, browser/computer, messaging, provider, tool, approval,
claim, scheduling, retry, or execution-fencing authority. No backend, domain,
persistence, migration, or runtime-resolution changes were required for Step 2.

Future Phase 23 work remains TBD after Step 2 review.

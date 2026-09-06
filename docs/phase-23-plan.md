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

## Step 2 — Task Skill Composition & Skill-Aware Launch Readiness ✅ complete

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

## Step 3 — Skill Usage Evidence & Run Feedback Operator UI

Step 3 exposes the existing Skill evidence and feedback substrate as an
operator observability and annotation surface. Skill Detail shows recent
factual usage evidence through the existing bounded Skill usage API. The
surface identifies the exact Run, Task, frozen Skill revision, position,
resolution, execution attempt, outcome, failure code, timing, tool-call count,
approval count, and evidence creation time. Nullable facts remain explicitly
unrecorded rather than being invented.

The usage surface is labelled as recent evidence and explains that the API
currently exposes up to 100 most recent materialized records. The browser does
not reconstruct evidence from Run history, read current Skill state into
historical rows, or load full revision instructions for every row. A usage row
can navigate to its exact Run through Friday's existing `?view=run&id=...`
routing model without opening an external URL or reloading the application.

Every usage record continues to show the immutable `revision_id` frozen on the
Run. A later Skill activation, disable, or archive does not replace that
historical revision or hide the usage ledger. Unresolved historical Runs remain
consistent with the backend's materialization rules; an empty response is
therefore described as no materialized evidence, not proof that a Skill was
never used. Usage loading and errors are isolated from Skill lifecycle and
revision inspection.

Run Detail extends each actual resolved frozen Skill item with append-only
operator feedback. Feedback is read and written only through the existing
Run/Skill SDK methods and is eligible from the exact frozen binding, without a
new terminal-Run restriction. Unresolved Skill resolution and resolved Runs
with zero Skills do not render a feedback form. Each feedback item displays its
rating, note, creator, timestamp, and frozen revision ID; all returned records
remain visible, including multiple records for one Run/Skill use.

The only ratings are `helpful`, `neutral`, and `harmful`. `created_by` is an
explicit required operator field with the existing 128-character maximum; no
fake authenticated identity is supplied. Notes remain optional and are sent
without transformation with the existing 4,000-character maximum. Feedback
creation retries are disabled, duplicate submission is disabled while pending,
and successful writes invalidate/refetch the exact Run/Skill feedback query.
The UI never inserts optimistic history. A keyed feedback panel owns local
draft state for the exact `run_id`, `skill_id`, and frozen `revision_id`, so
drafts cannot cross Skills or Runs.

Feedback responses are verified against the surrounding Run, Skill, and frozen
revision before display. A mismatch fails closed with an operator-visible
provenance error rather than rewriting response identifiers. Feedback remains
an annotation on the exact frozen use: it does not rewrite the Run outcome,
frozen revision, or factual usage evidence. A failed usage outcome can coexist
with helpful feedback; neither value is converted into the other, and no
success-rate, quality, effectiveness, harmfulness, confidence, or causal score
is calculated.

Submitting feedback performs no improvement or lifecycle action. There is no
proposal, evidence snapshot, evaluation, promotion, rollback, recommendation,
automatic disable, activation, generated revision, or other self-improvement
control in this Step. Historical feedback and provenance remain inspectable
when the current Skill is later disabled, archived, or pointed at another
revision. The distinction remains explicit: evidence is observation, feedback
is annotation, and neither grants execution authority.

No backend, domain, persistence, migration, worker, runtime, or SDK production
changes were required for Step 3. The authority boundary remains unchanged:
the Agent decides and reasons, Friday orchestrates and owns authority, Skills
influence reasoning only, and the protected Run → AgentRunProcessor →
reasoning → proposed action → risk assessment → ApprovalRequest when required
→ ToolInvocation → ToolGateway → actual execution path is untouched. Skills
never confer filesystem, process/shell, MCP, browser/computer, messaging,
provider, tool, approval, scheduling, claim, retry, or execution authority.

Future Phase 23 work remains TBD after Step 3 review.

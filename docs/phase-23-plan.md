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

Task Skill composition, self-improvement controls, generated revision
authoring, restore/unarchive, enable/re-enable redesign, and other future
operator workflows are not included.

Future Phase 23 work remains TBD after Step 1 review.

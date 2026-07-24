# Approval, Tool Invocation & Artifact Use Cases

Phase 8 owns application orchestration of durable authorization and
execution metadata only. It executes no tool, subprocess, model, or
network request; it schedules no expiry; it adds no worker, HTTP surface,
or object storage; it integrates no runtime. Every mutating command runs
in one Unit of Work, commits exactly once on success, and rolls back
completely on any exception.

## Approval state matrix

| Command | Valid source | Target | Coordination | Event batch (in order) |
| --- | --- | --- | --- | --- |
| RequestApproval | Run running (and step running when scoped) | pending | Run (and optional RunStep) `wait_for_approval` | `approval_requested`, `run_waiting_for_approval` |
| ApproveRequest | pending | approved | resume waiting Run/RunStep | `approval_resolved`, `run_resumed` |
| RejectRequest | pending | rejected | resume waiting Run/RunStep | `approval_resolved`, `run_resumed` |
| CancelApproval | pending | cancelled | resume waiting Run/RunStep | `approval_resolved`, `run_resumed` |
| ExpireApproval | pending, deadline due | expired | resume waiting Run/RunStep | `approval_resolved`, `run_resumed` |

Post-resolution state is `running` for every resolution: Phase 8 records
the authorization outcome and returns the waiting entity to `running` via
the existing `resume()` transition. What the runtime then does with a
rejection or expiry (fail, abandon, re-plan) is deferred to the runtime
phases — rejection is a human "no", cancellation a system withdrawal, and
neither implies the Run itself must terminate. An entity that stopped
waiting on that approval in the meantime (e.g. cancelled) is left
untouched; only the `approval_resolved` event is appended then.

A Run may hold at most one pending approval: `RequestApproval` conflicts
while `list_pending_for_run` is non-empty. `expires_at` is optional; it is
validated at creation (aware UTC, strictly after `requested_at`) and
persisted, and `ExpireApproval` conflicts while `now < expires_at` or when
no deadline exists. No scheduler runs in Phase 8 — expiry is an explicit
command. Phase 10 added the scheduler: `ExpireDueApprovals` (see
[worker-coordination.md](worker-coordination.md)) calls it on a bounded
maintenance interval.

### Approval idempotency

| Situation | Behavior |
| --- | --- |
| Same terminal resolution replayed | success, original timestamps/resolver/note preserved, no event |
| Different terminal resolution requested | `EntityConflict` |
| Approval not found | `ApprovalNotFound` |
| Owning Run/RunStep missing or mismatched | `RunNotFound` / `RunStepNotFound` / `EntityConflict` |

## ToolInvocation lifecycle

| Command | Valid source | Target | Output/Failure | Event | Replay |
| --- | --- | --- | --- | --- | --- |
| RequestToolInvocation | Run running (and step running when scoped) | requested | none | `tool_invocation_requested` | new identity each call |
| MarkToolInvocationRunning | requested | running | none | `tool_invocation_started` | running: idempotent, no event |
| MarkToolInvocationSucceeded | running | succeeded | JSON-validated output preserved | `tool_invocation_succeeded` | identical output idempotent; different output conflicts |
| MarkToolInvocationFailed | running | failed | structured `Failure` preserved | `tool_invocation_failed` | identical failure idempotent; different failure conflicts |
| CancelToolInvocation | requested, running | cancelled | none | `tool_invocation_cancelled` | cancelled: idempotent; succeeded/failed conflict |

Authorization rule: `approval_request_id` on an invocation is optional.
When present, the approval must exist, belong to the same Run, match the
step scope when the approval declares one, and be `approved`. An
invocation without a reference is recorded as unauthorised-by-omission;
whether that is permitted is a runtime-phase policy. Phase 7 cancellation
propagation (Task/Run/RunStep cancellation cancels non-terminal
invocations through `LifecycleEvents.cancel_tools`) is reused unchanged —
`CancelToolInvocation` is the same domain transition and event type.

## Artifact policy

- Identity: Artifact ID only. Matching checksum/location never implies
  the same Artifact; no content-addressable behavior exists.
- Ownership: every Artifact belongs to a running Run; an optional RunStep
  owner must exist and belong to that Run. Invalid ownership is rejected
  before anything is staged.
- `checksum`, `size`, `media_type`, `location`, and JSON-validated
  `metadata` are recorded exactly as supplied — nothing reads the
  location or computes checksums.
- Duplicate policy: `RecordArtifactCommand.artifact_id` is optional.
  Replaying the same ID with identical data returns the original
  recording (original `created_at`, no second row, no second event); the
  same ID with different data is `EntityConflict`.
- Reads: `GetArtifact`, `ListArtifactsForRun` (ordering `created_at, id`).
  `artifact_created` is the single canonical event.

## Cross-reference integrity

The `approval_request_id` columns on `runs`, `run_steps`, and
`tool_invocations` intentionally have no database foreign key (accepted
Phase 5 schema decision). Application validation is therefore the only
integrity layer for these references and remains mandatory:

- every referenced entity is loaded and must exist,
- RunStep and ApprovalRequest ownership must match the owning Run,
- approval step scope must match the invocation's step,
- rejected references leave no partial rows and no events (proved by
  fresh-session tests in `tests/persistence/test_phase8_use_cases.py`).

## Transactions and events

One Unit of Work per command; reads never commit; every mutation commits
once. Multi-event batches allocate one `next_sequence` and consecutive
values; event IDs and aware-UTC timestamps are application-allocated
(injected Clock). Idempotent replays append nothing. SQLAlchemy failures
surface only as `EntityConflict` / `ConcurrencyConflict` /
`TransactionFailure`. Fault injection at every coordination boundary
(entity staging, run/step save, sequence allocation, event append, flush,
commit, and resolution-before-resume) proves no partial durable state.

## Deferred boundaries

HTTP delivery (Phase 9); worker claiming, cross-worker sequence
allocation, retry scheduling, and approval-expiry scheduling (Phase 10);
actual tool execution and approval interception (Phase 11); object
storage (future scope).

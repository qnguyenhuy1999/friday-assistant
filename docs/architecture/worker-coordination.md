# Phase 10 — Durable Worker Coordination, Claims, Leases & Retry Scheduling

`apps/worker` claims durable work over the Phase 6–9 lifecycle instead of
running anything itself. It adapts persistence and timing; it does not
decide retry policy, lifecycle rules, or event ordering — those stay in
`friday.application` and `friday.domain`. No tool, model, or subprocess
execution exists in this phase; `RunProcessor` (the execution boundary) is
a `Protocol` with no concrete implementation until Phase 11.

## Work-item persistence model

`run_work_items` (`src/friday/infrastructure/persistence/models.py:65`,
migration `migrations/versions/0004_run_work_items.py`) is a one-row-per-Run
durable queue: `run_id` (primary key, FK to `runs.id`), `available_at`,
`enqueued_at`, `claimed_by`, `claim_token`, `claim_generation` (defaults to
`0`), `lease_expires_at`. Indexes on `available_at` and `lease_expires_at`
back the due/expired queries; a composite index on
`(available_at, enqueued_at, run_id)` backs deterministic candidate
ordering. A Run has at most one row — presence means "still work to do,"
absence means finalized (succeeded/failed terminally) or parked
(waiting for approval).

`SqlAlchemyRunWorkQueue` (`src/friday/infrastructure/persistence/work_queue.py`)
implements the application-owned `RunWorkQueue` port: `enqueue`,
`find_due_candidates`, `find_expired_claims`, `try_claim`, `renew_lease`,
`release_claim`, `requeue_claimed`, `remove_if_claimed`,
`remove_if_lease_expired`, `is_claim_active`, `remove`. Every mutating
method takes the full fencing triple (or claims none exists yet) and
returns a boolean/row-count rather than raising on a lost race — losing a
race is an ordinary outcome, not an error.

## Claim lifecycle and fencing

`ClaimNextRun` (`src/friday/application/worker_coordination.py`) reads
due candidates ordered by `(available_at, enqueued_at, run_id)`, and for
each one atomically calls `try_claim(run_id, worker_id, token, now,
lease_expires_at)` — a conditional `UPDATE ... WHERE claimed_by IS NULL`
that returns whether *this* call won the row. On failure it moves to the
next candidate; it never treats a lost race as an error. `claim_token` is
a fresh `uuid4().hex` per claim; `claim_generation` increments only when a
lease is forcibly recovered (`RecoverExpiredLeases`), never on ordinary
completion.

Every subsequent operation on a claimed Run — `RenewRunLease`,
`ReleaseRunClaim`, `RequeueClaimedRun`, `CompleteRunWorkItem`,
`ApplyFailedOutcome`, `ApplySucceededOutcome`, `ApplyWaitingOutcome` — is
fenced by the full `(worker_id, claim_token, claim_generation)` triple.
If any element mismatches (stale worker, wrong token, superseded
generation), the underlying `UPDATE`/`DELETE` matches zero rows and the
use case raises `ClaimLost` rather than silently no-op'ing or leaking a
raw persistence exception. This is how a worker that lost its lease is
fenced out: it cannot renew, release, complete, or schedule a retry for a
Run someone else now owns.

## Lease duration and heartbeat

The claiming worker holds a lease until `lease_expires_at`
(`now + lease_duration` at claim time). `WorkerLoop`
(`apps/worker/worker_loop.py:24`) runs a dedicated heartbeat thread per
claimed Run: every `heartbeat_interval_seconds` it calls
`RenewRunLease.execute(...)` with the same fencing triple. A `ClaimLost`
during renewal sets a `threading.Event` (`lease_lost`) rather than
crashing the loop; that event is exposed to the processor via
`ClaimContext.is_lease_lost` so in-flight work can observe and react to
losing ownership. The heartbeat thread is joined before the outcome is
applied — no renewal happens after processing finishes, and no session is
held open while the processor runs or while the loop sleeps between polls.

`WorkerSettings` (`apps/worker/settings.py`) requires
`heartbeat_interval_seconds` to leave enough margin under
`lease_duration` that a renewal has time to land before expiry; defaults
are a 60s lease against a 20s heartbeat.

## At-least-once delivery and expired-claim recovery

A claim is a delivery guarantee, not an execution guarantee: a worker can
be killed mid-processing, and its lease will eventually expire.
`RecoverExpiredLeases` (`src/friday/application/worker_maintenance.py`)
runs on the maintenance interval (separate from the poll/claim interval)
and, in bounded batches, either clears the claim on a still-runnable Run
(bumping `claim_generation` so the old worker's fencing triple stops
working) or removes the work item entirely if the Run has since reached a
terminal or waiting status. Recovery never changes `available_at` — a
recovered Run is immediately claimable again, not rescheduled.

Because recovery is at-least-once, Phase 11's `RunProcessor`
implementations must be idempotent: a Run's side effects may be attempted
by more than one worker if a lease expires mid-processing before the
first worker's outcome is durably applied.

## Queue enrollment matrix

| Lifecycle operation | Effect on `run_work_items` |
| --- | --- |
| Initial Run creation | Creates a row (`available_at = created_at`) |
| Manual retry | Creates a row for the new retry Run |
| Automatic retry (`ApplyFailedOutcome`, retry allowed) | Creates a row for the new retry Run, `available_at = now + backoff` |
| Manual start | Keeps the existing row (claim happens through the normal poll, not at start) |
| Approval resolution (resumed) | Creates a row for the resumed Run |
| Approval expiry (`ExpireDueApprovals`) | Creates a row for the resumed Run |
| Yielded outcome (`RequeueClaimedRun`) | Reschedules the existing row (`available_at` moves forward, claim cleared) |
| Waiting-for-approval outcome (`ApplyWaitingOutcome`) | Removes the row (parked; no active work until resolution/expiry) |
| Succeeded outcome (`ApplySucceededOutcome`) | Removes the row (terminal) |
| Failed outcome, no retry allowed (`ApplyFailedOutcome`) | Removes the row for the failed Run (terminal); no new row created |
| Expired-lease recovery on a terminal/waiting Run | Removes the row (cleanup) |

Every enrollment point commits the work-item mutation in the same
`UnitOfWork` transaction as the Run/event mutation it accompanies — a
rollback (from a mid-transaction failure) leaves neither a dangling event
nor an orphaned work item.

## Retry scheduling

`RetryPolicy` (`src/friday/application/retry_policy.py`) is a pure,
stateless calculator: `is_retry_allowed(attempt_number, failure)` returns
`failure.retryable and attempt_number < max_attempts` — a non-retryable
`Failure` never retries regardless of attempt count, and `max_attempts`
counts the original attempt (so `max_attempts=3` allows attempts 1 and 2
to retry, not attempt 3). `compute_delay(next_attempt_number)` is
deterministic exponential backoff, `base_delay * multiplier **
(next_attempt_number - 2)`, capped at `max_delay`, with no jitter — attempt
2 always waits exactly `base_delay`.

`ApplyFailedOutcome` is the only place a retry Run is created: it fences
the claim first, applies the `failed` lifecycle event to the source Run
(which stays failed — a retry is a new Run, not a resurrection of the
old one), and only then — if `is_retry_allowed` says yes — creates a new
`Run`, enqueues it at `now + compute_delay(...)`, and appends the
retry-scheduled event. Because the claim is fenced before any of this
runs, a stale worker cannot create a duplicate retry for a Run someone
else already finalized.

## Approval expiry maintenance

`ExpireDueApprovals` (`src/friday/application/worker_maintenance.py`)
runs on the maintenance interval in bounded batches: it finds pending
`ApprovalRequest`s past their expiry time, resolves each as expired, and
re-enqueues the Run it was blocking (mirroring approval-resolution
enrollment). Already-resolved or not-yet-due approvals are left alone;
concurrent expiry attempts on the same approval are safe because
resolution itself is guarded the same way lifecycle mutations always are
(read-check-write inside one `UnitOfWork`).

## Atomic event sequence reservation

Prior to Phase 10, `RunEvent`/`TaskEvent` sequence numbers were assigned
by reading `max(sequence) + 1` outside any atomicity guarantee — a race
between two concurrent appenders on the same Run/Task could produce a
duplicate sequence, caught only as a UNIQUE-constraint violation at flush
time. Phase 10 replaces this with dedicated per-Run/per-Task counter
tables (`migrations/versions/0005_event_sequence_counters.py`) and an
atomic reservation call: reserving a range of N sequence numbers is one
conditional `UPDATE ... SET next_sequence = next_sequence + N RETURNING
next_sequence - N`, so concurrent reservations against the same counter
serialize at the database rather than the application layer. A rolled-back
transaction never consumes a sequence number — the counter row is only
touched by the same transaction that appends the events using it. All
existing event-appending call sites (`LifecycleEvents.append_run_events`,
the equivalent for `TaskEvent`) go through this reservation API; none
computes `max(sequence) + 1` anymore.

## Worker loop

`WorkerLoop.run_once(processor)` (`apps/worker/worker_loop.py:52`) returns
`False` immediately if `processor is None` (maintenance-only mode never
claims) or if no work is due. Otherwise it claims one Run, starts the
heartbeat thread, calls `processor.process(context)`, joins the heartbeat
thread, and dispatches the `ProcessingOutcome` to the matching coordination
use case (`succeeded` → `ApplySucceededOutcome`, `failed` →
`ApplyFailedOutcome`, `waiting_for_approval` → `ApplyWaitingOutcome`,
`yielded` → `RequeueClaimedRun`). A `ClaimLost` raised while applying the
outcome is logged and swallowed — the claim was already lost to another
worker or recovery, so there is nothing left to do.

`run_maintenance_tick()` calls `RecoverExpiredLeases.execute()` and
`ExpireDueApprovals.execute()` and logs the counts. `serve_forever(
shutdown_event, processor=None)` drives both on independent intervals: a
monotonic-clock-gated maintenance tick, and `run_once` on every iteration,
falling back to `shutdown_event.wait(poll_interval_seconds)` (an
interruptible sleep, not a blocking `time.sleep`) whenever there was
nothing to claim. `processor=None` runs maintenance only — no claim is
ever attempted without a configured processor.

## Maintenance-only mode

`WorkerSettings.maintenance_only` (env `FRIDAY_WORKER_MAINTENANCE_ONLY`)
signals a deployment that should run `RecoverExpiredLeases` and
`ExpireDueApprovals` without claiming or processing Runs — useful for
running maintenance on a schedule separate from claiming workers.
`apps/worker/main.py` passes `processor=None` to `serve_forever` (Phase 11
supplies a real processor); a `None` processor and `maintenance_only=True`
currently produce the same claiming behavior (none), since no processor
wiring exists yet.

## Graceful shutdown

`apps/worker/main.py` installs `SIGTERM`/`SIGINT` handlers that set a
`threading.Event` (`shutdown_event`); `serve_forever` checks it every
iteration and every wait, so shutdown lands within one poll interval, and
`worker.engine.dispose()` runs in a `finally` block regardless of how the
loop exits.

## SQLite concurrency strategy

All coordination reads-then-writes (`try_claim`, `renew_lease`,
`release_claim`, `requeue_claimed`, `remove_if_claimed`, sequence
reservation) are expressed as single conditional `UPDATE` statements with
a `WHERE` clause matching the expected prior state (`claimed_by IS NULL`,
or the exact fencing triple) — never a `SELECT` followed by an
unconditional write. This makes each operation safe under SQLite's
single-writer model without needing explicit application-level locking:
two connections racing the same `UPDATE` are serialized by SQLite itself,
and exactly one sees its `WHERE` clause match.

## Explicit non-goals

Phase 10 does not implement Claude, BrainRuntime, ToolGateway, MCP,
browser control, subprocess execution, or any real tool/model execution.
A claim does not guarantee exactly-once execution — see at-least-once
delivery above. `RunProcessor` is a `Protocol` with no concrete
implementation; Phase 11 must supply one, and Phase 11's side effects must
be idempotent because recovery can redeliver a claim. Phase 11's worker
application operations must go through the claim-aware coordination use
cases documented here, not around them.

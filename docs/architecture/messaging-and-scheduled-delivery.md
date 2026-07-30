# Messaging and scheduled delivery

Phase 19 will make outbound messaging a durable workflow: an approved message intent is bound to a route, persisted, dispatched through a transport, and recorded with an unambiguous outcome. Scheduled runs may later create delivery intents through an explicit policy and fire-plan bridge.

## Implemented: Step 1

Step 1 implements only the durable substrate. `OutboundDelivery` records immutable authority, source identity, route binding, and message content; its lifecycle supports `queued → sending`, `sending → delivered|failed|ambiguous`, and `queued → cancelled`. The `outbound_deliveries` schema, repository, and unit-of-work wiring preserve those records and enforce one delivery per tool invocation or schedule fire.

## Implemented: Step 2

Step 2 makes delivery ownership and the external side-effect boundary durable, so a later dispatcher can send at most one external message per delivery and any crash is recoverable into an honest state. It adds no transport and performs no network I/O.

### The dispatch boundary invariant

A claimed delivery must distinguish two situations that look identical from outside. `dispatch_started_at` is the durable marker separating them:

```text
QUEUED
  -> claimed SENDING, dispatch_started_at = NULL     (nothing was sent)
  -> dispatch boundary, dispatch_started_at = <time> (a send may have happened)
  -> future external I/O
```

Without this marker, recovery has only bad options: treating every expired `SENDING` as `QUEUED` risks duplicate external messages, and treating every expired `SENDING` as `AMBIGUOUS` loses deliveries that crashed before anything was sent. `MarkDeliveryDispatchStarted` commits the marker in its own short transaction, which a future dispatcher must execute immediately before its first external write.

The marker is one-way: set once, never moved backward, never cleared. In the domain, `deliver()` and `mark_ambiguous()` are illegal before the boundary is crossed (both imply a send may exist), while `fail()` stays legal for a definite pre-dispatch failure. `release_for_retry()` is legal only *before* the boundary.

### Recovery

`RecoverExpiredDeliveryClaims` resolves every expired lease deterministically, using the existing `RetryPolicy` for bounded backoff rather than a second retry implementation:

| Expired `SENDING` claim | Outcome |
| --- | --- |
| `dispatch_started_at IS NULL`, retry budget remains | back to `QUEUED` with `available_at = now + RetryPolicy` backoff; `attempt_count` and `claim_generation` preserved |
| `dispatch_started_at IS NULL`, budget exhausted | `FAILED` with `delivery_pre_dispatch_attempts_exhausted` |
| `dispatch_started_at IS NOT NULL` | `AMBIGUOUS` with `delivery_lease_expired_after_dispatch`, never resent automatically |

Failure codes are Friday-owned and stable; recovery never persists untrusted external error text.

### Claim fencing

`ClaimNextDelivery` opens one short unit of work, recovers expired claims, lists a bounded set of due `QUEUED` candidates, and attempts an atomic claim per candidate. Claiming is a single fenced `UPDATE` on `(id, status = queued, available_at <= now)` that sets `SENDING`, the claim owner and token, `claim_generation + 1`, the lease expiry, and `attempt_count + 1` — never a read-modify-write. Two workers racing on the same row therefore produce exactly one successful claim. No transaction wraps external I/O.

Every subsequent mutation is fenced on delivery id, `status = sending`, exact claim owner, exact claim token, exact `claim_generation`, and an unexpired lease (equality with `claim_expires_at` counts as expired). Stale or expired claims fail closed with `ClaimLost`. Because the generation advances on each new claim, a worker from a recovered generation can never overwrite the recovered state:

```text
generation 1 claims -> lease expires -> recovery -> generation 2 claims
                                     -> every generation-1 write matches no row
```

Fenced outcome writes (`PersistDeliveryOutcome`) update lifecycle columns only. Route binding, subject, body, body digest, and source identity are never in the `SET` clause, so no worker — current or stale — can retarget a delivery through an outcome write. `attempt_count`, `claim_generation`, and `dispatch_started_at` are likewise excluded: they change only through `try_claim` and `mark_dispatch_started`.

## Deferred Phase 19 work

## Implemented: Step 3

Step 3 adds operator-owned route configuration and the `message.send` tool. The
model sees only enabled route aliases and bounded trusted descriptions; endpoint
URLs, environment-variable names, and credentials never enter the tool manifest,
approval payload, provenance, or delivery row.

```text
operator-owned routes -> model-visible aliases only -> exact external-communication approval
-> durable enqueue -> zero external effect
```

Every `message.send` assessment uses an `EXTERNAL_COMMUNICATION` approval scope
whose route fingerprint covers safe authority fields and a digest of the
in-memory endpoint. The authorization fingerprint also covers exact tool input,
so changing the body, route, delivery time, or route authority invalidates an old
approval. Execution creates exactly one queued `OutboundDelivery`, keyed by tool
invocation for replay safety. No transport, HTTP client, dispatcher, or polling
loop is constructed in this step.

`ToolInvocation SUCCEEDED` for `message.send` means durable enqueue succeeded,
not that an external delivery occurred.

Step 4 implements route-authoritative webhook dispatch: a claimed delivery is checked against the configured route, then crosses its durable dispatch boundary before network I/O. Missing, disabled, or fingerprint-drifted routes become pre-dispatch `AMBIGUOUS` with no network I/O.

Step 5 adds `DeliveryAttempt`, a durable, secret-free ledger keyed by `(delivery_id, claim_generation)`. In one short transaction the exact active claim marks `dispatch_started_at` and creates an `IN_PROGRESS` attempt; only after commit may webhook I/O begin. A terminal transport result closes the matching attempt and delivery in the same fenced transaction. Expired post-dispatch claims close their matching in-progress attempt as `AMBIGUOUS` in the same recovery transaction. No attempt is created for route/authority ambiguity before the boundary.

An attempt is one-way and cannot be created without a crossed boundary. There is no generic repository `add`/`save`: the only creation path is `begin_for_claim`, an `INSERT ... SELECT` whose fencing is enforced inside the database — it writes nothing unless the delivery is `SENDING`, owned by that exact `(worker_id, claim_token, claim_generation)`, its lease is unexpired, and `dispatch_started_at` already equals the boundary being recorded. `UNIQUE(delivery_id, claim_generation)` is the concurrency fence. Identity (`id`, `delivery_id`, `claim_generation`, `started_at`) is immutable and lifecycle state (`outcome`, `finished_at`, `failure_code`) moves only through `complete()`, which validates every input before mutating anything, so a rejected completion leaves the record unchanged. The same lifecycle rules are enforced a second time as SQLite `CHECK` constraints, and `failure_code` is always a stable lowercase code of at most 128 characters — never provider or exception text. History reads are bounded: `list_for_delivery` requires `1 <= limit <= MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT` (1000), because a negative `LIMIT` is unbounded in SQLite.

Migration `0014` backfills one attempt per delivery that already had `dispatch_started_at` set before the ledger existed, reconstructed only from timestamps the old schema recorded truthfully. Without it, post-dispatch recovery — which fails closed when the matching attempt is absent — would leave those deliveries permanently unrecoverable. Pre-dispatch rows get no attempt, and data that cannot yield truthful audit history fails the migration rather than inventing a row.

Generic webhook delivery currently has no verified provider idempotency contract. Therefore post-dispatch `FAILED` and `AMBIGUOUS` outcomes are never automatically retried. Automatic redrive requires a future provider-idempotency design.

## Scheduled delivery authority

`ScheduleDeliveryPolicy` is direct operator intent for future fires. It stores only a schedule-owned route alias and enabled state—never an endpoint, credential, or fingerprint. When the existing scheduler materializes a `ScheduleFire`, it resolves that alias through the worker's loaded messaging configuration and writes an immutable `ScheduleFireDeliveryPlan` for that occurrence.

The plan freezes the route fingerprint and execution lineage, or a stable suppressed reason when the route is missing or disabled. Policy and route changes therefore affect future fires only; historical fires receive no migration backfill and are never retroactively authorized. This phase creates no message content, `OutboundDelivery`, network call, or agent-answer read.

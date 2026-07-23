# Lifecycle Use Cases

Phase 7 owns durable state orchestration only. It is synchronous, uses one
application-owned Unit of Work per command, and has no HTTP, worker claim,
lease, execution, approval-resolution, or external tool cancellation behavior.

## Transition and idempotency matrix

| Entity | Valid Phase 7 transitions | Terminal states | Repeating target | Other terminal target |
| --- | --- | --- | --- | --- |
| Task | pending to active (Phase 6 StartRun); active to completed or failed; pending or active to cancelled | completed, failed, cancelled | success, no event | conflict |
| Run | queued to running; running to succeeded or failed; queued/running/waiting to cancelled | succeeded, failed, cancelled | success, no event | conflict |
| RunStep | pending to running or skipped; running to succeeded or failed; pending/running/waiting to cancelled | succeeded, failed, skipped, cancelled | success, no event | conflict |

Failure replay succeeds only when the preserved `Failure` compares equal;
different failure details are a conflict and never overwrite the original.
Every idempotent success still uses the standard single successful `commit()`
policy, but stages no writes or events.

## Coordination policy

`StartRun` retains its Phase 6 meaning: it creates a new queued Run.  Only
`StartQueuedRun` performs `queued -> running`. A terminal Run or RunStep is
immutable. A failed Run leaves its Task active and retryable; completion and
cancellation of a Run never complete or cancel its Task.

The current persisted Run representation has no numeric attempt column.
Attempt order is therefore `created_at, id`; retry requires the source failed
Run to be the latest member in that order and creates a distinct queued Run.
If any non-terminal Run exists, retry conflicts. This intentionally avoids
guessing duplicate-retry idempotency without an idempotency-key system.

Steps use a non-negative position unique within a Run. `CreateOrderedStep`
allocates `max(position) + 1`; a concurrent duplicate position is translated
by the Unit of Work to `EntityConflict`.

## Cancellation and events

`CancelTask` mutates the Task then every non-terminal Run in `created_at,id`
order. `CancelRun` and Task propagation process steps in `position,id` order,
then ToolInvocations in `requested_at,id` order. Terminal children are left
unchanged. Tool cancellation is durable metadata only.

For a Run, the observable event batch begins with `run_cancelled`, followed
by step cancellation, its tool cancellations, and run-owned tool cancellations. Each Run
gets one `next_sequence()` call and consecutive event sequence values. The
accepted cross-worker `next_sequence`/append race remains Phase 10 work.

Existing `RunEvent` is intentionally non-nullably run-owned, so Task-only
transitions cannot be attached to a fabricated or unrelated Run. Phase 7 adds
the narrow canonical `TaskEvent` contract and per-Task sequence store for
`task_completed`, `task_failed`, and `task_cancelled`; this is not a generic
event bus.

## Deferred boundaries

Phase 8 owns approval, tool, and artifact use cases. Phase 10 owns durable
worker claiming, cross-worker event sequence allocation, retry scheduling,
and backoff. Phase 11 owns actual tool execution/cancellation.

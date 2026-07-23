# Domain Model

This document describes the Phase 4 domain model under `src/friday/domain`.
It covers the entities, value objects, and lifecycle rules the codebase
enforces. Application use-case orchestration and transaction coordination
are outside this document; persistence is described separately in
[persistence.md](persistence.md).

## Value Objects

- **Identifiers** (`identifiers.py`) — `TaskId`, `RunId`, `RunStepId`,
  `RunEventId`, `ApprovalRequestId`, `ArtifactId`, `ToolInvocationId`. Each
  wraps a canonical UUID string, is immutable, and is its own type: two
  identifiers built from the same UUID string but different types never
  compare equal (`TaskId.parse(x) != RunId.parse(x)`).
- **Time** (`time.py`) — `ensure_utc` rejects naive datetimes and converts
  any timezone-aware datetime to UTC. Every entity timestamp passes through
  this.
- **JSON values** (`json_value.py`) — `ensure_json_value` recursively
  validates that a value is JSON-wire-compatible (no NaN/Infinity,
  datetimes, sets, tuples, or non-string mapping keys), raising
  `DomainValidationError` with a JSONPath-style location (e.g. `$.a[0]`) on
  the first violation.
- **Failure** (`failure.py`) — the structured failure record every
  fallible entity carries: non-empty `code`/`message`, a `retryable` flag,
  a `FailureCause` enum (`validation`, `tool`, `runtime`, `approval`,
  `cancelled`, `timeout`, `internal`), and JSON-compatible `details`.
  Deliberately not a wrapped exception, to keep domain state serializable
  and vendor-free.
- **Errors** (`errors.py`) — `DomainValidationError` (a value violates an
  invariant) and `InvalidStateTransition` (an entity attempted a lifecycle
  transition it does not allow), both under a common `DomainError` base.

## Entities

Each entity is a `@dataclass(slots=True)` with private fields and
read-only properties. Mutation only happens through named lifecycle
methods that validate the current status before transitioning
(`_require_status`), so illegal transitions raise `InvalidStateTransition`
rather than silently corrupting state.

| Entity | Status enum | Terminal statuses |
| --- | --- | --- |
| `Task` | `TaskStatus` | `completed`, `failed`, `cancelled` |
| `Run` | `RunStatus` | `succeeded`, `failed`, `cancelled` |
| `RunStep` | `RunStepStatus` | `succeeded`, `failed`, `skipped`, `cancelled` |
| `ApprovalRequest` | `ApprovalStatus` | `approved`, `rejected`, `cancelled`, `expired` |
| `ToolInvocation` | `ToolInvocationStatus` | `succeeded`, `failed`, `cancelled` |

`RunEvent` and `Artifact` are frozen (`@dataclass(frozen=True, slots=True)`)
records, not lifecycle entities — they are created once and never
transition.

### Task

`pending -> active -> {completed, failed}`, or `pending|active -> cancelled`.
`Task.new` trims `title`/`description` and rejects a blank title.

### Run

`queued -> running -> {succeeded, failed}`, with an optional detour through
`waiting_for_approval` (`running <-> waiting_for_approval` via
`wait_for_approval`/`resume`). Cancellable from `queued`, `running`, or
`waiting_for_approval`. `succeed`/`fail`/`cancel` validate the end
timestamp is not before the run's start (or creation, if never started) —
`_validated_end` raises `DomainValidationError` otherwise.

### RunStep

Same shape as `Run`, plus a `pending -> skipped` transition (skip is only
legal before the step starts) and position-based ordering
(`RunStep.new` rejects a negative `position`).

### ApprovalRequest

`pending` resolves exactly once, to `approved`, `rejected`, `cancelled`, or
`expired` — every resolution method (`approve`/`reject`/`cancel`/`expire`)
requires `pending` and is a one-way transition. `new` trims `summary` and
`requested_action` and rejects either being blank.

### ToolInvocation

`requested -> running -> {succeeded, failed}`, cancellable from `requested`
or `running`. `succeed` requires an explicit `output` argument — a sentinel
(`_UNSET`) distinguishes "no output provided" from "output is `None`",
since `None` is itself a valid JSON output value.

### RunEvent

An append-only fact (`RunEventType` enum covers run/step/approval/tool/
artifact lifecycle events), not an event-sourced aggregate: it is not the
source of truth for reconstructing `Task`/`Run`/`RunStep` state in this
phase. Requires a positive `sequence`; sequence allocation is an
application-layer concern (`RunEventStore.next_sequence`), not enforced by
the entity itself.

### Artifact

Metadata only — never reads/writes bytes or manages storage. Rejects a
blank `name`/`location` or a negative `size`.

## Application Ports

`src/friday/application/ports.py` defines `Protocol`s for one repository
per entity plus `RunEventStore` and `Clock`. Ports are structural
(`typing.Protocol`), not ABCs. The Phase 5 SQLite adapter implements these
ports; application use cases are documented separately when delivered.
Each `list_for_*` method documents its required ordering (e.g. runs by
`created_at` then `id`) as part of the port contract, not left to each
future implementation to decide independently.

## Test Coverage

- `tests/domain/` — one file per value-object/entity module. Entity tests
  are table-driven over every status in the enum: one test per legal
  transition, one parametrized test asserting every illegal source status
  is rejected, and one test proving every terminal status rejects all
  further transitions.
- `tests/application/test_ports.py` — minimal in-memory fakes proving each
  port `Protocol`'s shape is structurally satisfiable and exercising its
  documented ordering contract.

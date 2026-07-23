# Persistence Layer

This document describes the Phase 5 SQLite persistence adapter under
`src/friday/infrastructure/persistence`, which implements the seven
repository/store ports declared in `src/friday/application/ports.py`.

## Module Split

- **`database.py`** — engine and session-factory construction only:
  `create_engine` (SQLite connection + PRAGMA setup) and
  `create_session_factory`. No table or mapping knowledge.
- **`models.py`** — the SQLAlchemy ORM table definitions (`Base` and one
  `Row` class per table). No domain imports, no query logic.
- **`mappers.py`** — explicit `X_to_row`/`X_from_row` function pairs, one
  per entity. The only place an ORM row and a domain object are converted
  between each other. No generic/reflection-based mapper: field renames or
  type changes surface as a type error at the call site.
- **`repositories.py`** — the port implementations (`TaskRepository`,
  `RunRepository`, `RunStepRepository`, `ApprovalRepository`,
  `ArtifactRepository`, `ToolInvocationRepository`, `RunEventStore`). This
  is the only public surface application code depends on; no ORM object
  ever crosses a repository method's return boundary — every method
  returns or accepts domain objects, converting via `mappers.py`
  internally.

This split exists so each concern changes independently: swapping ORM
frameworks touches `models.py`/`mappers.py`, not `repositories.py`'s
call sites in application code; adding a table touches `models.py` and one
mapper pair, not the engine setup.

## Schema and Foreign-Key Graph

Seven tables, created via Alembic migration `migrations/versions/0001_initial_schema.py`:

```text
tasks
  └─< runs (task_id -> tasks.id)
        ├─< run_steps (run_id -> runs.id)
        ├─< approval_requests (run_id -> runs.id, step_id -> run_steps.id)
        ├─< artifacts (run_id -> runs.id, step_id -> run_steps.id)
        ├─< tool_invocations (run_id -> runs.id, step_id -> run_steps.id)
        └─< run_events (run_id -> runs.id, step_id -> run_steps.id)
```

All of `task_id`, `run_id`, and `step_id` columns above are real
`ForeignKey` constraints pointing strictly backward to an
already-created parent table.

### Cross-reference columns without FK constraints

`runs.approval_request_id`, `run_steps.approval_request_id`, and
`tool_invocations.approval_request_id` are optional cross-references to
`approval_requests` — a table created *after* all three of them in
table-creation order. Rather than rely on SQLite's forgiving (but
easy-to-get-subtly-wrong) deferred-name-resolution behavior for
`REFERENCES` clauses, the plan keeps these three columns as plain indexed
columns with **no DB-level FK constraint**, documented explicitly as a
scope decision here.

## PRAGMA Choices

`create_engine` in `database.py` sets three PRAGMAs on every new SQLite
connection:

- `PRAGMA foreign_keys=ON` — SQLite does not enforce declared foreign keys
  unless this is set per-connection.
- `PRAGMA journal_mode=WAL` — better concurrent read/write behavior for a
  file-backed database. **Limitation:** WAL mode has no effect on an
  in-memory database (SQLite ignores it) — tests against `sqlite://`
  verify `foreign_keys` only, not `journal_mode`.
- `PRAGMA busy_timeout=5000` — waits up to 5 seconds for a locked database
  instead of failing immediately, absorbing brief writer contention.

## `next_sequence`/`append` Non-Atomicity

`RunEventStore.next_sequence` computes `max(sequence) + 1` for a run via a
separate `SELECT` from the later `INSERT` that `append` performs — the two
are not one atomic operation. Two concurrent callers computing
`next_sequence` before either has appended could both compute the same
value. This is deliberately not fixed with a transaction/locking scheme in
this phase; instead, `run_events` has
`UniqueConstraint("run_id", "sequence", name="uq_run_events_run_id_sequence")`
as a backstop — a duplicate-sequence append for the same run fails at
flush time with `IntegrityError` rather than silently corrupting event
order (see `tests/persistence/test_run_event_store.py::test_duplicate_sequence_for_same_run_is_rejected_at_db_level`).

## No `UnitOfWork` Port

`ports.py`'s module docstring states the reasoning directly:

> No UnitOfWork port: nothing in this phase requires multiple repository
> writes inside one shared transaction boundary, and adding one now would
> be speculative. Introduce it once persistence in a later phase
> demonstrates a concrete need.

This phase does not add one either — callers share a single `Session` and
call `session.flush()`/commit at the call site.

## Schema Source of Truth

Outside tests, **Alembic migrations** (`migrations/versions/`) are the
schema source of truth, applied via `alembic upgrade head`. Application
and production code never calls `Base.metadata.create_all()`. Tests use
`Base.metadata.create_all()` only as a fast, migration-independent way to
stand up a throwaway schema for unit-testing repositories
(`tests/persistence/conftest.py`); `tests/persistence/test_migrations.py`
separately proves the real Alembic migration produces the same seven
tables (plus `alembic_version`) and that downgrade-then-upgrade is
idempotent.

# Phase 9 — API Delivery & Contract Adapters

`apps/api` exposes the Phase 6–8 application use cases over a local HTTP
API. It adapts; it does not decide. No lifecycle rule, authorization
check, or event-ordering guarantee lives in a route — those stay in
`friday.application` and `friday.domain`.

## Composition root

```text
ApiSettings.from_env()
    -> create_engine(database_url)
    -> create_session_factory(engine)
    -> create_unit_of_work_factory(session_factory)  -> app.state.uow_factory
                                        SystemClock() -> app.state.clock
    -> register_exception_handlers(app)
    -> app.include_router(...)  one router per entity
```

`apps/api/app.py:create_app(settings)` is the sole place infrastructure is
constructed; tests call it directly with a temporary SQLite path instead
of importing the process entry point (`apps/api/main.py`), so no test run
ever touches a real `friday.db`. One `UnitOfWork` is opened per request via
FastAPI's dependency injection (`apps/api/dependencies.py`); none is held
open across a request boundary. Startup never runs
`Base.metadata.create_all()` — schema stays exclusively Alembic-owned.

## Boundary models and error mapping

Every route has an explicit Pydantic request/response model in
`apps/api/schemas/`, mapped to/from application commands and results by
hand (`to_command()`/`from_result()` methods) — never a raw
`.model_dump()` into a domain constructor, never an ORM row returned
directly.

`apps/api/errors.py` is the single `ApplicationError` -> HTTP mapper,
registered once as FastAPI exception handlers:

| Application error | HTTP | `error.type` |
| --- | --- | --- |
| `TaskNotFound` | 404 | `task_not_found` |
| `RunNotFound` | 404 | `run_not_found` |
| `RunStepNotFound` | 404 | `run_step_not_found` |
| `ApprovalNotFound` | 404 | `approval_not_found` |
| `ToolInvocationNotFound` | 404 | `tool_invocation_not_found` |
| `ArtifactNotFound` | 404 | `artifact_not_found` |
| `EntityConflict` | 409 | `entity_conflict` |
| `ConcurrencyConflict` | 409 | `concurrency_conflict` |
| `TransactionFailure` | 500 | `transaction_failure` (fixed, safe message — never the raw exception text) |
| `RequestValidationError` / malformed cursor | 422 | `validation_error` |

Response body: `{"error": {"type", "message", "details"}}`. No route
catches a SQLAlchemy exception or imports an ORM model — connectivity
checks (`/health`) go through `friday.infrastructure.persistence.health`
instead of touching `sqlalchemy` from delivery code.

## Pagination

Cursor format is owned entirely by `apps/api/pagination.py`
(`page_ordered`/`encode_cursor`/`decode_cursor`). A cursor is a versioned,
base64-encoded tuple of the *last-seen item's sort-key values*
(e.g. `(created_at, id)`), not a raw offset — so a page stays stable if
items are inserted or removed between reads. Repository ports return full
ordered lists rather than accepting a cursor themselves; the API layer
fetches the documented order and slices it. That's a scoped choice for
this phase's SQLite scale — moving to real keyset SQL queries would only
change what `list_ordered`'s callers pass in, not the cursor's shape.

Default page size 25, max 100 (`DEFAULT_PAGE_SIZE`/`MAX_PAGE_SIZE`). An
invalid `limit` or malformed `cursor` is a 422, never a 500.

| Collection | Order |
| --- | --- |
| Tasks | `created_at`, then `id` |
| Runs for Task | `created_at`, then `id` |
| RunSteps for Run | `position`, then `id` |
| Approvals for Run | `requested_at`, then `id` |
| ToolInvocations for Run/Step | `requested_at`, then `id` |
| Artifacts for Run | `created_at`, then `id` |
| RunEvents / TaskEvents | `sequence` |

## Events and SSE

`RunEvent` and `TaskEvent` are separate canonical streams with separate
read endpoints (`GET /v1/runs/{run_id}/events`, `GET
/v1/tasks/{task_id}/events`) — there is no merged event sequence.

`GET /v1/runs/{run_id}/events/stream` is the only SSE endpoint (RunEvent
only; TaskEvent streaming is out of scope for this phase). It:

- 404s before opening the stream if the Run doesn't exist (an initial
  `ListRunEvents` call doubles as the existence check).
- Polls on a short-lived `UnitOfWork` per tick (`settings.sse_poll_interval_seconds`)
  — no session is held open across the connection.
- Frames each event as `id: <sequence>` / `event: <type>` / `data: <json>`,
  using the same response schema as the plain list endpoint.
- Honors `Last-Event-ID` to resume after a sequence; a malformed header
  value is rejected (422) rather than crashing the stream.
- Stops cleanly on client disconnect (`request.is_disconnected()`).

## OpenAPI

Title `Friday Agent OS API`, version `0.1.0`, all business routes under
`/v1`. Every operation has an explicit `operation_id` (44 total, all
unique — see `tests/api/test_openapi.py`). Generation is deterministic
and covered by a snapshot-free structural test (expected paths present,
no `*Row` ORM type leaking into `components.schemas`).

## Non-goals (explicitly out of scope for this phase)

`POST /v1/runs/{run_id}/start` transitions queued -> running only — it
never claims a Run, assigns worker ownership, or creates a lease (Phase
10). ToolInvocation endpoints mutate metadata only; nothing is actually
executed (Phase 11). Approval endpoints record a decision; nothing is
automatically triggered by it. Artifact endpoints record metadata only —
no upload/download or object storage. No WebSockets, no generic event
bus, no TypeScript SDK generation (Phase 14).

## Running locally

```bash
uv run python -m apps.api.main
```

Binds to `127.0.0.1:8000` by default. Environment variables:
`FRIDAY_API_DATABASE_URL` (default `sqlite:///./friday.db`),
`FRIDAY_API_HOST` (default `127.0.0.1`), `FRIDAY_API_PORT` (default
`8000`), `FRIDAY_API_SSE_POLL_INTERVAL_SECONDS` (default `0.5`). Run
Alembic migrations before starting against a fresh database file — the
app never runs `metadata.create_all()` itself.

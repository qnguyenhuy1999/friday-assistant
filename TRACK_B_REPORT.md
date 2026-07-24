# Phase 9 Track B report

Track B delivers FastAPI contract adapters for approvals, tool-invocation
metadata, artifacts, Run/Task events, and a Run-event SSE stream.

- Added delivery-owned request/response schemas with explicit command/result
  mapping, opaque API-layer pagination, and thin routes wired from the API
  composition root.
- Added `ListApprovalsForRun`, `ListRunEvents`, and `ListTaskEvents` read use
  cases. `TaskEventStore` gained `list_for_task`, implemented by the SQLite
  repository and the application fakes, ordered by sequence.
- SSE verifies Run existence before opening, reads in short-lived UnitOfWork
  polls, emits `id`/`event`/JSON `data` frames, and honors `Last-Event-ID`.
- Added integration coverage for all endpoint lifecycle paths, error mapping,
  pagination, and SSE sequencing/reconnect/disconnect behavior. Tool endpoint
  tests explicitly establish that they persist metadata only.

## Design decisions

Pydantic schemas type JSON payload fields as `typing.Any`. Pydantic 2.13
recurses while building models using the domain's recursive `JsonValue` alias;
the domain and application layers retain `JsonValue`, while delivery models
avoid the framework recursion. Track A independently encountered and fixed
the same issue with the same boundary-local workaround, keeping both tracks
consistent.

Pagination uses an opaque base64-encoded offset at the API boundary because
the established application list ports return complete, deterministically
ordered lists. It validates malformed cursors and invalid limits as 422.

The route parameter annotations leave defaults on the function signature,
not inside `Annotated[Query(...)]`: current FastAPI rejects duplicate query
defaults in that form during import. This keeps the application importable
while retaining request validation.

## Verification

- `uv run mypy` — passed (124 files)
- `just check` — passed
- `uv run pytest -W error -q` — passed (516 passed, 2 skipped)
- Explicit API/Python architecture boundary tests — passed
- `just test-cov` — passed

No open questions. No task/run/run-step delivery files were changed.

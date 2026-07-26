# Friday local operations runbook

Friday is local-first. The API binds to `127.0.0.1` by default, and computer
use is disabled unless explicitly enabled. Claude proposes actions; the worker
remains the only component that validates, authorizes, executes, and persists
them.

## Bootstrap and start

```bash
just bootstrap
uv run alembic upgrade head
just worker-check
uv run python -m apps.api.main
just worker
pnpm --filter @friday/web dev
```

`just doctor` is an alias for the same full non-mutating worker preflight.
`GET /health` is liveness: an `ok` response means the API process can answer.
`GET /ready` is readiness: it returns `200 {"status":"ok"}` only when the
configured database is reachable and its Alembic schema is at head; otherwise
it returns `503 {"status":"unavailable"}`. Worker dependencies are checked
by `just worker-check`, which performs the existing non-mutating preflight for
database, schema, Claude's brain-only CLI mode, workspace, and enabled
computer-use driver. `just memory-check` reports memory configuration without
reading the vault.

## Scheduled automations

Schedules are durable timing records attached to one Task. They only create a
queued Run; they never execute Claude actions or tools, and every scheduled
Run follows the ordinary validation and approval path. Use the web control
plane's **Schedules** action for a Task, or task-scoped API endpoints, to
create a one-time (`run_at`) or five-field cron schedule. Use an IANA timezone
such as `Asia/Ho_Chi_Minh`; stored fire timestamps are UTC.

For a one-time schedule, submit wall time together with its IANA timezone
(the web UI does this automatically); a past time is rejected rather than
silently completing the schedule. At DST fall-back, an ambiguous wall time
uses the earlier instant (`fold=0`); a nonexistent spring-forward wall time
is rejected. Cron calculations follow the same IANA timezone policy.

The worker evaluates schedules during its normal maintenance cadence.
`FRIDAY_SCHEDULER_ENABLED=false` disables materialization without deleting
schedule state; `FRIDAY_WORKER_MAINTENANCE_BATCH_SIZE` bounds each tick. On
restart, schedules resume from persisted state. Missed cron occurrences are
coalesced to the next future occurrence, and a schedule never overlaps its
own non-terminal execution, including its retry descendants. An occurrence
blocked by that execution stays due (it is deferred, not dropped) and creates
exactly one overdue Run when the prior execution is terminal. Inspect schedule
fires to correlate an occurrence to its single materialized Run.

For a schedule that should stop creating Runs, use **Pause** (resumable) or
**Cancel** (terminal). Cancelling a Task cancels its active/paused schedules;
completing or failing a Task completes them. If a schedule appears due but no
Run is created, first check that the Task is non-terminal and that an earlier
schedule Run has reached a terminal state. Scheduler logs use the
`scheduler.materialized` event and never include prompts, tool input, or
secrets. `FRIDAY_SCHEDULER_ENABLED` is strict: set it to a recognized boolean
(`true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`) or worker startup fails.

## Common diagnosis

- `worker-check` reports `claude_brain_only` failed: install/sign in to the
  local Claude CLI, or set `FRIDAY_CLAUDE_EXECUTABLE` to the intended binary.
  Do not replace it with a general shell-capable agent.
- A memory check fails: memory is opt-in. Disabled memory is healthy. When
  enabled, a real vault root and explicit `FRIDAY_MEMORY_INCLUDE_GLOBS`
  allowlist are mandatory; invalid enabled configuration fails startup and
  preflight rather than silently disabling memory. Graphify stays healthy when
  disabled; when enabled, its executable must be available to preflight.
- The worker does not progress: check `/ready`, then run `just worker-check`.
  Inspect the run, steps, tool invocations, and durable event stream through
  the web control plane/API. Pending approvals must be resolved there; they
  never auto-approve after a restart.
- The event stream is degraded: the web UI shows an alert and periodically
  backfills durable event history. Control-plane reads and approvals remain
  available.
- A run fails: use its durable failure code, event timeline, steps, and tool
  invocations as the source of truth. Worker JSON logs are correlation-only
  diagnostics (`task_id`, `run_id`, `worker_id`, `claim_generation`); they do
  not determine lifecycle state and deliberately omit exception messages,
  paths, command text, and secrets.

## Safe configuration and restart

`FRIDAY_API_HOST` accepts loopback addresses only. Remote binding is rejected
because the API has no authenticated transport; CORS is not an authentication
boundary. Use exact `http(s)` origins in `FRIDAY_API_CORS_ORIGINS`.
`FRIDAY_API_MAX_REQUEST_BYTES` defaults to 1 MiB and rejects oversized request
bodies with HTTP 413 before route parsing.

To restart, stop API and worker with `SIGTERM`, then start them using the
commands above. Claims, approvals, events, and terminal outcomes are durable.
Workers lease claims and fence application of outcomes, so a terminal run is
not replayed and a stale worker cannot apply a late outcome. An ambiguous
protected tool execution is not blindly replayed.

On `SIGTERM`, the worker stops taking new loop iterations. An active processor
observes shutdown through its claim fence; its outcome is requeued rather than
applied, so it cannot start a subsequent tool action or persist a terminal
outcome after shutdown begins. Work that crossed a protected side-effect
boundary before the signal remains subject to the existing ambiguity fencing;
it is never blindly replayed.

To enable computer use, set `FRIDAY_COMPUTER_USE_ENABLED=true`, configure the
driver limits in `.env.example`, and run `just worker-check` before starting a
worker. Mutating computer actions still require approval and semantic
revalidation.

## Release gate

```bash
just check       # format, lint, types, Python/TS, architecture, policy, contracts
just e2e         # real browser → web → API → worker → durable result
just ci          # full local release-equivalent gate
```

The CI workflow runs on pull requests and `main`. Review approval is valid
only when CI is green for the exact commit being reviewed.

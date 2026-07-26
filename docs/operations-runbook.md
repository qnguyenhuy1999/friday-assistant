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
`GET /ready` is readiness: it is `ok` only when the configured database is
reachable and its Alembic schema is at head. Worker dependencies are checked
by `just worker-check`, which performs the existing non-mutating preflight for
database, schema, Claude's brain-only CLI mode, workspace, and enabled
computer-use driver. `just memory-check` reports memory configuration without
reading the vault.

## Common diagnosis

- `worker-check` reports `claude_brain_only` failed: install/sign in to the
  local Claude CLI, or set `FRIDAY_CLAUDE_EXECUTABLE` to the intended binary.
  Do not replace it with a general shell-capable agent.
- A memory check reports degraded or disabled: memory is opt-in. When enabled,
  set a real vault root and an explicit `FRIDAY_MEMORY_INCLUDE_GLOBS` allowlist.
  Friday falls back to no memory context rather than scanning an unbounded
  personal vault.
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

`FRIDAY_API_HOST` accepts loopback binding by default. Binding a non-loopback
host requires `FRIDAY_API_ALLOW_REMOTE_BIND=true`; wildcard CORS origins are
rejected. Use exact `http(s)` origins in `FRIDAY_API_CORS_ORIGINS`.

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

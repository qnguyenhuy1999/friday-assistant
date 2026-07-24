# Worker App

## Owns

- The worker delivery process's composition root (`app.py` and `main.py`).
- The worker loop, heartbeat/lease-renewal supervisor, graceful shutdown, and
  maintenance-only mode.
- Wiring together the completed infrastructure and application use cases.

## Must Not Own

- Domain rules, use cases, or infrastructure adapters — those belong in
  `src/friday/`.
- Queue libraries or concrete run processing.
- A concrete `RunProcessor` implementation — that is Phase 11.

## May Compose

- `friday.infrastructure`
- `friday.application`
- `friday.domain`

## Current Status

The delivery process has a real composition root and worker loop. It claims
due runs, supervises each processor call with a heartbeat that renews its
lease, applies the resulting outcome, and periodically runs bounded lease and
approval maintenance. `FRIDAY_WORKER_MAINTENANCE_ONLY=true` runs the
maintenance scheduler without a processor. The process handles SIGTERM and
SIGINT and disposes its engine during shutdown. A concrete `RunProcessor` is
deferred to Phase 11.

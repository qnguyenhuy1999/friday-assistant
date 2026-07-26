# API App

## Owns

- The API delivery process's composition root (`main.py`).
- Wiring together `friday.infrastructure`, `friday.application`, and
  `friday.domain` once those layers have real behavior.

## Must Not Own

- Domain rules, use cases, or infrastructure adapters — those belong in
  `src/friday/`.
- Framework code, HTTP routing, or database access.

## May Compose

- `friday.infrastructure`
- `friday.application`
- `friday.domain`

## Operations

Run the API with `uv run python -m apps.api.main`. It binds to loopback by
default. `GET /health` is liveness and `GET /ready` additionally verifies that
the database is reachable and migrated to the current Alembic head. See the
[operations runbook](../../docs/operations-runbook.md) for startup, preflight,
diagnosis, and safe configuration.

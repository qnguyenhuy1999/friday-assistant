# Contracts Package

Owns language-neutral schemas and shared protocol definitions between
processes (API, worker, web, SDK).

## Policy

- Contracts must be language-neutral where possible.
- JSON Schema will be the canonical format for external event/request/
  response contracts.
- Generated language bindings must not become the source of truth.
- Contracts must be versioned deliberately; breaking changes require
  explicit review.
- No real API contract exists yet — no schemas for tasks, runs, events,
  approvals, or tools have been added in this phase.

## Current Status

Package shell only. `src/index.ts` exports a static metadata object to
validate workspace layout and type-checking.

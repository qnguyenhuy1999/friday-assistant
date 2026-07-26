# SDK Package

Owns the TypeScript client-facing SDK surface.

## Policy

- Consumes canonical wire contracts from `@friday/contracts`.
- Must not redefine backend business rules.

## Current Status

`FridayClient` is the public entry point. It composes thin resources for
tasks, runs, steps, approvals, tool invocations, artifacts, events, and
health. `events.streamForRun()` wraps the API's existing named-event SSE
endpoint; all lifecycle decisions remain caller-owned.

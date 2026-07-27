# Architecture Overview

This document describes the implemented source organization through Phase 18:
domain and application logic, SQLite persistence, API and worker delivery,
Claude runtime, memory, computer use, contracts, SDK, web control plane, and
durable scheduled automations.

## Source Tree

- **`src/friday/domain`** — pure business types, rules, and domain-owned
  interfaces. No outward dependency.
- **`src/friday/application`** — use cases and orchestration. May depend
  on `domain` only.
- **`src/friday/application/conversation_context.py`** — bounded, Run-scoped
  conversational history for the agent runtime (see
  [conversation-interface.md](conversation-interface.md)).
- **`src/friday/infrastructure`** — adapters to databases, external APIs,
  and the filesystem. May depend on `application` and `domain`.
- **`src/friday/infrastructure/persistence`** — SQLite adapter via
  SQLAlchemy/Alembic implementing the application ports (see
  [persistence.md](persistence.md)).
- **`apps/api`** — API delivery process. A thin composition root exposing
  Phase 6–8 use cases over local HTTP (see
  [api-delivery.md](api-delivery.md)).
- **`apps/worker`** — worker delivery process. A composition root plus a
  claim/lease/retry coordination loop over the Phase 6–9 use cases (see
  [worker-coordination.md](worker-coordination.md)), the `AgentRunProcessor`
  brain loop (see [agent-runtime.md](agent-runtime.md)), memory index
  maintenance, and the durable schedule dispatcher.
- **`src/friday/infrastructure/computer`** — the opt-in desktop computer-use
  substrate behind `ComputerToolGateway` (see
  [computer-use.md](computer-use.md)). Reachable only from
  `infrastructure/tools/computer_gateway.py` and
  `infrastructure/tools/computer_composition.py`; the brain runtime,
  application layer, domain layer, and worker loop must never import it.
- **`src/friday/infrastructure/mcp`** — the opt-in Friday-owned MCP
  integration substrate behind `McpToolGateway` (see
  [mcp-integrations.md](mcp-integrations.md)). Reachable only from
  `infrastructure/tools/mcp_gateway.py` and
  `infrastructure/tools/mcp_composition.py`; the brain runtime, application
  layer, domain layer, and worker loop must never import it. MCP is a
  transport behind Friday's authority model, not a second way to reach one.
- **`apps/web`** — React/Vite browser control plane for tasks, runs,
  approvals, artifacts, events, final results, and schedules.
- **`apps/web/src/voice`** — browser-native speech adapters and controller;
  voice is delivery only and has no server-side execution surface.
- **`packages/contracts`** — language-neutral schemas and cross-process
  protocol definitions (see [contracts.md](contracts.md)).
- **`packages/sdk-ts`** — validated TypeScript client SDK for the HTTP and
  event-stream contract.
- **`tests/domain`** — domain entity/value-object unit and state-machine
  tests (see [domain-model.md](domain-model.md)).
- **`tests/application`** — application port structural-typing tests.
- **`tests/contracts`** — JSON Schema validity, reference, example, and
  compatibility tests.
- **`tests/architecture`** — import-boundary and repository-layout
  checks.
- **`tests/policy`** — dependency, repository, provenance,
  sensitive-file, and Markdown-link policy checks. Structural, not
  architectural, but enforced the same way (see
  [../governance/quality-gates.md](../governance/quality-gates.md)).
- **`tests/toolchain`** — Phase 1 toolchain smoke test.
- **`tests/persistence`** — SQLite repository, mapper, database, and
  migration tests (see [persistence.md](persistence.md)).

## Dependency Direction

```text
apps/api ───────┐
apps/worker ────┼──> infrastructure ──> application ──> domain
apps/web ───────┘

packages/contracts  independent protocol source
packages/sdk-ts     consumes contracts, never app internals
```

An arrow means "may depend on." `domain` has no outward dependency.
`application` depends on `domain` only. `infrastructure` and the
deployable apps depend inward on `application` and `domain`, never the
reverse.

## Enforcement

- **Python:** `tests/architecture/test_python_boundaries.py` parses every
  module under `src/friday/{domain,application,infrastructure}` with
  `ast` and asserts each file's imports stay within its layer's allowed
  set. A negative fixture test (`test_detector_flags_a_forbidden_domain_import`)
  proves the detector actually flags a violation, using a synthetic
  source string rather than mutating real files. Run via `just test`.
- **TypeScript:** `packages/contracts` and `packages/sdk-ts` have no
  dependency on `apps/web` in their `package.json` — pnpm's workspace
  resolution would fail to resolve such a dependency at
  `pnpm install` time (no app is published as a consumable workspace
  dependency of a package), and `tsc --build` (via `just typecheck`)
  would fail to resolve any such import at the type level. There is no
  separate script for this because the only interfaces cross-package
  code can currently import are the static metadata exports in each
  package's `src/index.ts`.
- **Repository layout:** `tests/architecture/test_repository_layout.py`
  asserts no generic `utils`/`helpers`/`common`/`shared` directory
  exists, no Python application file sits directly at the repository
  root, and no tracked source file under `src`, `apps`, `packages`, or
  `tests` is empty.

## Status

Implemented through **Phase 17 — Conversational Voice Interface**:

- **Phase 4–5** — framework-independent domain model, application ports, JSON
  Schema contracts, and the SQLite persistence adapter (see
  [persistence.md](persistence.md)) with Alembic as the schema source of truth.
- **Phase 6–9** — application kernel, lifecycle use cases, approval/tool/
  artifact use cases, and the FastAPI delivery boundary (see
  [api-delivery.md](api-delivery.md), [lifecycle-use-cases.md](lifecycle-use-cases.md),
  [approval-tool-artifact-use-cases.md](approval-tool-artifact-use-cases.md)).
- **Phase 10–11** — leased worker coordination with claim fencing (see
  [worker-coordination.md](worker-coordination.md)) and the Claude-CLI brain
  runtime with gateway-mediated tool execution (see
  [agent-runtime.md](agent-runtime.md)).
- **Phase 12–13** — structural memory retrieval over the canonical Obsidian
  vault (see [../memory.md](../memory.md)) and opt-in desktop computer use
  (see [computer-use.md](computer-use.md)).
- **Phase 14–16** — TypeScript SDK and React control plane over generated
  versioned contracts (see [contracts.md](contracts.md)), operational
  hardening, and durable scheduled automations.
- **Phase 17** — durable conversational turns, bounded Run-scoped context,
  and browser-native voice delivery (see
  [conversation-interface.md](conversation-interface.md)).

Later phases add Friday-owned MCP and external integrations, a messaging
gateway, skills and self-improvement, and multi-agent delegation. None of
those are implemented yet.
The adapter and migration behavior are covered by `tests/persistence`.

Later phases add the FastAPI delivery boundary, leased worker coordination,
Claude/tool execution, curated memory, computer use, TypeScript SDK and React
control plane, operational hardening, and durable scheduled automations.

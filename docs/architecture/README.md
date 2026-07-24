# Architecture Overview

This document describes the source organization established through
Phases 4 and 5. It covers structure and dependency boundaries — the domain
model, application ports, contracts, and SQLite persistence adapter are
implemented; no API routes, worker, AI integration, or frontend behavior
exists yet.

## Source Tree

- **`src/friday/domain`** — pure business types, rules, and domain-owned
  interfaces. No outward dependency.
- **`src/friday/application`** — use cases and orchestration. May depend
  on `domain` only.
- **`src/friday/infrastructure`** — adapters to databases, external APIs,
  and the filesystem. May depend on `application` and `domain`.
- **`src/friday/infrastructure/persistence`** — SQLite adapter via
  SQLAlchemy/Alembic implementing the application ports (see
  [persistence.md](persistence.md)).
- **`apps/api`** — API delivery process. A thin composition root exposing
  Phase 6–8 use cases over local HTTP (see
  [api-delivery.md](api-delivery.md)).
- **`apps/worker`** — worker delivery process. A thin composition root.
- **`apps/web`** — browser control-plane delivery. A thin TypeScript
  package shell (no React/Vite yet).
- **`packages/contracts`** — language-neutral schemas and cross-process
  protocol definitions (see [contracts.md](contracts.md)).
- **`packages/sdk-ts`** — TypeScript client SDK surface. No generated
  client exists yet.
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

Phase 4 adds a framework-independent domain model, application ports, and
JSON Schema contracts. Those domain and contract surfaces are complete for
the entities documented in their respective modules and tests.

Phase 5 adds a SQLite persistence adapter (see
[persistence.md](persistence.md)) implementing all seven application
ports via SQLAlchemy, with Alembic migrations as schema source of truth.
The adapter and migration behavior are covered by `tests/persistence`.

Phase 9 adds the FastAPI/Uvicorn/Pydantic API delivery boundary (see
[api-delivery.md](api-delivery.md)): a composition root, centralized
error mapping, cursor pagination, and a Run-event SSE stream over the
Phase 6–8 use cases. The worker, AI/runtime integration, and the
frontend still do not exist.

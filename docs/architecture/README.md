# Architecture Overview

This document describes the source organization established in Phase 2.
It covers structure and dependency boundaries only — no business logic,
framework, database, or AI integration exists yet.

## Source Tree

- **`src/friday/domain`** — pure business types, rules, and domain-owned
  interfaces. No outward dependency.
- **`src/friday/application`** — use cases and orchestration. May depend
  on `domain` only.
- **`src/friday/infrastructure`** — adapters to databases, external APIs,
  and the filesystem. May depend on `application` and `domain`.
- **`apps/api`** — API delivery process. A thin composition root.
- **`apps/worker`** — worker delivery process. A thin composition root.
- **`apps/web`** — browser control-plane delivery. A thin TypeScript
  package shell (no React/Vite yet).
- **`packages/contracts`** — language-neutral schemas and cross-process
  protocol definitions. No real contracts exist yet.
- **`packages/sdk-ts`** — TypeScript client SDK surface. No generated
  client exists yet.
- **`tests/architecture`** — import-boundary and repository-layout
  checks.
- **`tests/toolchain`** — Phase 1 toolchain smoke test.

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

Phase 2 is structure only. Real business models, use cases, adapters,
routes, and contracts are introduced in later, separately reviewed
phases.

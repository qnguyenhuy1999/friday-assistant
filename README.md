# Friday Agent OS

Local-first engineering agent operating system.

## Status

**Phase 3 — quality gates and architecture hardening.** The repository now
enforces formatting, linting, typing, tests, coverage, dependency/
repository/provenance policy, lockfile reproducibility, and shell/Markdown
hygiene locally (pre-commit), in a full local gate, and in CI — with no new
business logic, framework, database, or AI integration.

## Repository Structure

```text
apps/
├── api/        Python API delivery process (composition root)
├── worker/     Python worker delivery process (composition root)
└── web/        TypeScript browser control-plane shell (@friday/web)
src/friday/
├── domain/         business types and rules — no outward dependency
├── application/     use cases — may depend on domain only
└── infrastructure/  adapters — may depend on application and domain
packages/
├── contracts/  language-neutral protocol definitions (@friday/contracts)
└── sdk-ts/     TypeScript client SDK surface (@friday/sdk)
tests/
├── architecture/  import-boundary and repository-layout checks
├── policy/        dependency/repository/provenance/sensitive-file/link policy checks
└── toolchain/     Phase 1 toolchain smoke test
```

Dependency direction: `apps/*` and `infrastructure` may depend on
`application`, which may depend on `domain`; the arrow never points the
other way. `packages/sdk-ts` consumes `packages/contracts`, never
`apps/web` internals. See
[docs/architecture/README.md](docs/architecture/README.md) for the full
diagram and enforcement mechanism.

## Greenfield Implementation

Friday Agent OS is a clean-room, greenfield implementation. It is not a fork,
copy, or migration of any other repository. Where other systems (e.g. Javis
OS, Hermes Agent) are referenced during design, they are used only as
behavioral or product references — see
[docs/governance/provenance.md](docs/governance/provenance.md) for the exact
rules governing that.

## High-Level Product Goals

- Provide a local-first operating system for an engineering agent that can
  plan, execute, and verify software work.
- Keep a clear separation between domain logic, application use cases, and
  infrastructure/delivery concerns.
- Support tool and computer-use execution under explicit, reviewable policy
  controls.
- Favor structural code/knowledge retrieval and curated memory over ad hoc
  context stuffing.

## Phase 0 Scope

This phase establishes only the repository foundation:

- Git initialization on `main`
- Repository metadata (`README.md`, `CONTRIBUTING.md`, `SECURITY.md`)
- Editor and line-ending configuration (`.editorconfig`, `.gitattributes`)
- `.gitignore` for the anticipated Python/Node/local-first stack
- Governance documentation (provenance and repository rules)
- Architecture documentation describing future boundaries only

## Intentionally Not Implemented Yet

- No application source code (Python, Node, or otherwise)
- No frameworks (FastAPI, React, or similar)
- No Docker or CI configuration
- No database code or domain models
- No dependency manifests or lockfiles

## Planned Architecture Areas

See [docs/architecture/README.md](docs/architecture/README.md) for the
planned high-level architectural boundaries (domain, application,
infrastructure, contracts, SDK, worker, web control plane, tool gateway,
Claude runtime, structural retrieval, curated memory, and a computer-use
sidecar). None of this is implemented yet.

## Documentation

- [docs/architecture/README.md](docs/architecture/README.md) — future
  architectural boundaries
- [docs/governance/provenance.md](docs/governance/provenance.md) — rules on
  referencing or reusing external code
- [docs/governance/repository-rules.md](docs/governance/repository-rules.md)
  — cross-phase contribution rules
- [LICENSE](LICENSE) — MIT License
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [SECURITY.md](SECURITY.md) — security policy

## Development

**Phase status:** Phase 3 — quality gates and architecture hardening. No
application runtime exists yet (no API, no frontend, no database, no AI
integration) — only structure, dependency boundaries, static
composition-root shells, and the quality gates described below.

Required runtimes:

- Python 3.13+ (managed via [uv](https://docs.astral.sh/uv/))
- Node.js >=22 <25 (installed via Corepack-managed pnpm; developed against 22.23.1 "Jod" LTS)
- pnpm 11.16.0 (activated via Corepack from `packageManager` in
  `package.json`)
- [`just`](https://just.systems) as the local command runner

Bootstrap the toolchain:

```bash
just bootstrap
```

Run the full validation suite (format check, lint, typecheck, tests):

```bash
just check
```

See `justfile` for the complete list of available commands.

## Quality Gates

Formatting, linting, typing, tests, coverage, architecture/dependency/
repository/provenance policy, lockfile reproducibility, and shell/Markdown
hygiene are all enforced through the same small set of `just` recipes,
invoked identically by pre-commit hooks and CI. See
[docs/governance/quality-gates.md](docs/governance/quality-gates.md) for the
full gate-by-gate breakdown.

```bash
# Install git hooks (default stage + pre-push stage)
uv run pre-commit install
uv run pre-commit install --hook-type pre-push

just check       # fast, non-mutating local gate
just ci          # full CI-equivalent gate
just pre-commit  # run every configured hook against all files
```

## Development Status Disclaimer

This project is in an early, pre-release state. Interfaces, structure, and
scope are expected to change significantly between phases. Nothing in this
repository should be treated as stable or production-ready.

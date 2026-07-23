# Friday Agent OS

Local-first engineering agent operating system.

## Status

**Pre-implementation — repository foundation only.** This repository
currently contains only the repository foundation. No runtime, API,
frontend, AI integration, or tool execution exists yet.

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

## Development Status Disclaimer

This project is in an early, pre-release state. Interfaces, structure, and
scope are expected to change significantly between phases. Nothing in this
repository should be treated as stable or production-ready.

# Friday Agent OS

**Local-first personal AI operating layer with durable execution, explicit approval, structural memory, computer use, and scheduled automation.**

Friday combines ideas from agent systems such as [Javis OS](https://github.com/blogminhquy/javis-os) and [Hermes Agent](https://github.com/NousResearch/hermes-agent), while keeping execution authority inside a Friday-owned runtime.

Friday is a greenfield implementation, not a fork of either project.

## Core principle

```text
Claude proposes actions.
Friday validates, authorizes, executes, persists and fences actions.
```

Claude CLI is used as a subscription-backed reasoning engine.

Claude does not directly execute shell commands, write files, invoke MCP servers, or control the desktop. All side effects pass through Friday's application/runtime boundaries and `ToolGateway`.

## Current architecture

```text
                        Human
                          │
                    Web control plane
                          │
                       TS SDK
                          │
                         API
                          │
              ┌──────── Friday ────────┐
              │                        │
          Application               Worker
              │                        │
              │                  AgentRunProcessor
              │                        │
              └──────────┬─────────────┘
                         │
                    Claude CLI
                   reasoning only
                         │
                   proposes action
                         │
                  ExecuteToolAction
                         │
                    ToolGateway
                  ┌──────┼────────┐
                  │      │        │
             Workspace Memory  Computer
                       │          │
                    Obsidian   cua-driver
                       ↕
                    Graphify
```

Scheduled automation adds:

```text
Schedule
↓
ScheduleFire
↓
Run
↓
existing Worker/runtime
```

## Implemented

### Durable runtime

- Task / Run / RunStep lifecycle
- SQL persistence and Alembic migrations
- durable work queue
- worker claims and leases
- retries and restart recovery
- durable events
- artifact lifecycle

### Safety and approvals

- risk assessment
- exact approval fingerprints
- one-shot `ApprovalRequest` lifecycle
- durable `ToolInvocation` records
- worker claim fencing
- ambiguous non-idempotent execution protection

### Claude subscription brain

Friday uses locally authenticated Claude CLI credentials.

The Claude process is intentionally brain-only and cannot directly use filesystem, shell, MCP, or computer tools. Friday owns action validation, authorization, durable execution, and side-effect fencing.

### Memory

```text
Obsidian = canonical human-owned memory
Graphify = derived/rebuildable structural index
Friday   = retrieval, provenance, budgeting and write policy
```

Memory retrieval is bounded and structural results are re-read from the canonical vault before being inserted into context. Graphify remains disposable derived state rather than a source of truth.

### Computer use

Friday integrates `cua-driver` behind a `ComputerDriver` abstraction and `ComputerToolGateway`.

Claude cannot reach the driver directly. Desktop mutations use the same approval, claim-fencing, and durable execution path as other protected tools.

### API, SDK and Web

- FastAPI control plane
- generated versioned HTTP contracts
- TypeScript SDK
- React Web UI
- runtime response validation
- browser E2E coverage

The browser reaches Friday through the SDK and API; it does not directly reach persistence, worker internals, Claude, memory, or the computer driver.

### Durable scheduled automations

- one-shot schedules
- cron schedules
- IANA timezone support
- pause / resume / cancel
- `ScheduleFire` history
- durable occurrence idempotency
- overlap prevention across retry execution lineage
- downtime coalescing
- restart recovery
- normal approval semantics for scheduled Runs

A schedule decides when a Run exists; scheduled automation never executes tools directly.

## Repository structure

```text
apps/
├── api/
├── worker/
└── web/

src/friday/
├── domain/
├── application/
└── infrastructure/

packages/
├── contracts/
└── sdk-ts/

tests/
```

Dependency direction remains:

```text
delivery/infrastructure
        ↓
application
        ↓
domain
```

Domain never depends outward. See [docs/architecture/README.md](docs/architecture/README.md) for architecture documentation and [docs/governance/provenance.md](docs/governance/provenance.md) for the project's clean-room reference policy.

## Current roadmap

Completed core foundations through:

```text
Phase 16 — Durable Scheduled Automations
```

Remaining v1 phases:

```text
Phase 17 — Conversational Voice Interface
Phase 18 — Friday-owned MCP & External Integrations
Phase 19 — Messaging Gateway & Scheduled Delivery
Phase 20 — Skills & Self-Improvement Loop
Phase 21 — Agents, Workflows & Delegation
```

After Phase 21, additional work is considered product expansion rather than core architecture.

## Development

Required runtimes and tooling include Python 3.13+, Node.js >=22 <25, Corepack-managed pnpm, `uv`, and `just`.

Bootstrap:

```bash
just bootstrap
```

Fast validation:

```bash
just check
```

Full CI-equivalent validation:

```bash
just ci
```

Pre-commit:

```bash
just pre-commit
```

See `justfile` for the complete command set and `docs/` for architecture, governance, and operational documentation.

## Project status

Friday is actively developed and should still be treated as pre-release software.

The core execution/runtime foundations are implemented through Phase 16; voice, external MCP integrations, messaging, skills/self-improvement, and multi-agent workflows remain upcoming work.

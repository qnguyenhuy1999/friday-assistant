# Architecture Overview

This document describes the architectural boundaries Friday Agent OS is
expected to grow into. **None of these directories or modules exist yet.**
They will be created in the phase that actually implements them (planned for
Phase 2), not in Phase 0.

## Expected Future Boundaries

- **apps** — deployable entry points (web control plane, worker, etc.)
- **domain** — core business/domain logic, framework-agnostic
- **application** — use cases orchestrating domain logic
- **infrastructure** — adapters to databases, external APIs, filesystems
- **contracts** — shared interface/schema definitions between layers
- **SDK** — generated or hand-written client libraries
- **worker** — background/async task execution
- **web control plane** — the operator-facing web interface
- **tool gateway** — mediates access to external tools/actions
- **Claude runtime** — integration boundary with the Claude Agent SDK/API
- **Graphify structural retrieval** — code/knowledge graph retrieval layer
- **Obsidian curated memory** — human-curated long-term memory store
- **computer-use sidecar** — isolated execution surface for computer-use
  actions

## Intended Dependency Direction

```
domain <- application <- infrastructure and delivery applications
```

`domain` has no outward dependencies. `application` depends on `domain` only.
`infrastructure` and delivery applications (web, worker, CLI, etc.) depend
inward on `application` and `domain`, never the reverse.

## Status

This is a forward-looking description only. The concrete source tree,
module boundaries, and dependency wiring will be created and reviewed in
Phase 2. Phase 0 introduces no source code.

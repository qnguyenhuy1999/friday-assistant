# Contracts Package

Owns language-neutral schemas and shared protocol definitions between
processes (API, worker, web, SDK).

## Policy

- Contracts must be language-neutral where possible.
- JSON Schema (Draft 2020-12) is the canonical format for external
  event/request/response contracts.
- Generated language bindings must not become the source of truth.
- Contracts must be versioned deliberately; breaking changes require a new
  version directory, not an in-place edit.

## Schema Set (v1)

Canonical schemas live under `schemas/v1/`, one directory per entity, plus a
`definitions/` directory for shared shapes (`identifier`, `timestamp`,
`json_value`, `failure`) referenced via `$ref`. Each entity schema mirrors
its `friday.domain` counterpart field-for-field:

| Schema | Domain type |
| --- | --- |
| `task/task.json` | `friday.domain.task.Task` |
| `run/run.json` | `friday.domain.run.Run` |
| `step/run_step.json` | `friday.domain.step.RunStep` |
| `event/run_event.json` | `friday.domain.event.RunEvent` |
| `approval/approval_request.json` | `friday.domain.approval.ApprovalRequest` |
| `artifact/artifact.json` | `friday.domain.artifact.Artifact` |
| `tool/tool_invocation.json` | `friday.domain.tool.ToolInvocation` |

Every schema sets `additionalProperties: false` (except `json_value.json`,
which is deliberately open — it validates arbitrary JSON-compatible
payloads). See `docs/architecture/contracts.md` for the full versioning
policy and the domain-to-contract mapping rationale.

## Current Status

`src/index.ts` exports `CONTRACTS_VERSION` and a `schemaPath()` helper so
consumers resolve schema paths through one version-aware indirection point.
No runtime schema loading or code generation exists yet — that is later
tooling work, not part of this phase.

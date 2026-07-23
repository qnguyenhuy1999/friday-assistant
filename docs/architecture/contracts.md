# Contract Schemas

This document describes the Phase 4 `packages/contracts` schema set: what
it covers, how it maps to the domain model, and the versioning policy
`tests/contracts` enforces. It complements
[domain-model.md](domain-model.md), which describes the Python types these
schemas mirror.

## What Exists

Canonical JSON Schema (Draft 2020-12) definitions under
`packages/contracts/schemas/v1/`, one directory per entity plus a
`definitions/` directory for shapes referenced via `$ref` from more than one
entity schema:

| Common schema | Purpose |
| --- | --- |
| `definitions/identifier.json` | canonical UUID string, mirrors `friday.domain.identifiers._Id` |
| `definitions/timestamp.json` | RFC 3339 UTC-only datetime string, mirrors `ensure_utc` |
| `definitions/json_value.json` | recursive JSON-compatible value, mirrors `ensure_json_value`; the one schema left `additionalProperties`-open, since it validates arbitrary payloads by design |
| `definitions/failure.json` | mirrors `friday.domain.failure.Failure` |

Entity schemas (`task/task.json`, `run/run.json`, `step/run_step.json`,
`event/run_event.json`, `approval/approval_request.json`,
`artifact/artifact.json`, `tool/tool_invocation.json`) mirror their
`friday.domain` counterpart field-for-field and set
`additionalProperties: false`.

## Why a Separate Contract Layer

The domain model (`src/friday/domain`) is the source of truth for
behavior and invariants; it is Python-only and framework-free by rule (see
[domain-model.md](domain-model.md)). `packages/contracts` exists because
processes outside that Python codebase (a future TypeScript web client, an
SDK, an external caller) need a language-neutral description of the same
shapes without depending on Python or importing internal modules. The
schema is a wire contract, not a replacement for domain validation —
`ensure_json_value`, `ensure_utc`, and each entity's own invariants still
run in Python regardless of what a schema permits.

## Versioning Policy

- Schemas are versioned by directory (`v1/`), not by field. A breaking
  change (removing/renaming a required field, narrowing a type, changing
  an enum's wire values) requires a new `v2/` directory; existing `v1/`
  schemas are never edited in place once published.
- An additive, backward-compatible change (a new optional field, a new
  enum member appended to a `oneOf`/`enum` list) may be made in place
  within the current version, since it does not break an existing
  consumer validating already-conformant payloads.
- `packages/contracts/src/index.ts` exposes `CONTRACTS_VERSION` and a
  `schemaPath()` helper so consumers resolve schema paths through one
  version-aware indirection point rather than hardcoding `v1/` throughout
  calling code.

## Domain-to-Contract Mapping Rationale

Each entity schema mirrors its domain dataclass field-for-field rather
than the entity's public property names, because the wire contract
describes the entity's *state*, not its Python API surface. Enum fields
use the domain `StrEnum`'s string values directly as the JSON Schema
`enum` list — `tests/contracts/test_domain_compatibility.py`'s
`test_schema_enum_matches_domain_enum` fails if a domain enum gains,
loses, or renames a member without the corresponding schema being updated,
so schema/domain enum drift is caught in CI rather than discovered by a
downstream consumer at runtime.

## Enforcement (`tests/contracts/`)

- **Schema validity** — every schema file is valid Draft 2020-12
  (`test_schema_validity.py`).
- **Reference resolution** — every `$ref` resolves against an in-memory
  `referencing.Registry` built solely from `packages/contracts/schemas/`,
  and a monkeypatched `urllib.request.urlopen` proves no network I/O ever
  occurs during validation (`test_schema_references.py`). Contract
  validation must work fully offline.
- **Examples** — one realistic valid example per entity schema, plus
  negative cases (extra property, missing required field, invalid enum
  value) proving `additionalProperties: false` and `required` are actually
  enforced, not just declared (`test_schema_examples.py`).
- **Domain compatibility** — enum parity plus a real lifecycle projection
  per entity: a domain object is constructed and driven through its actual
  transition methods, then projected to its wire shape and validated
  against the schema (`test_domain_compatibility.py`).
- **Versioning** — structural checks that the `v1/` layout and
  `CONTRACTS_VERSION`/`schemaPath()` stay consistent
  (`test_contract_versioning.py`).

Every detector above ships with its own `test_detector_flags_...` negative
fixture, per the repository convention described in
[quality-gates.md](../governance/quality-gates.md#changing-a-policy-safely).

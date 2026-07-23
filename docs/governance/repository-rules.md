# Repository Rules

Rules that apply across all phases of Friday Agent OS development.

## Scope Discipline

- Implement only the active phase. Do not introduce work belonging to a
  future phase, even if it seems convenient to bundle it in.
- No placeholder folders (empty `apps/`, `src/`, `packages/`, `tests/`, etc.)
  created ahead of the phase that actually needs them.
- No dead code, no commented-out code.
- No speculative dependencies — a dependency is added only in the phase that
  actually uses it, and only with a clear purpose.

## Code and File Hygiene

- No generated files committed unless a specific phase intentionally requires
  it (and documents why).
- No secrets, credentials, tokens, or local runtime data committed.
- No unlicensed source copying — see [provenance.md](provenance.md).
- Every module has a clear owner and a clear reason for existing.

## Process

- Small, focused commits. One logical change per commit.
- Documentation is updated in the same change that introduces the structural
  shift it describes.
- New dependencies must state their purpose in the commit or PR description.
- When dependency tooling is introduced, the latest stable versions are
  selected at that time, and lockfiles are committed alongside.
- Dependency upgrades are deliberate and tested, not incidental, and are
  reported explicitly rather than bundled silently into unrelated changes.

## Related Documents

- [provenance.md](provenance.md) — rules for referencing or reusing external
  code
- [../architecture/README.md](../architecture/README.md) — planned
  architectural boundaries
- [../../CONTRIBUTING.md](../../CONTRIBUTING.md) — contribution workflow

# Contributing

Friday Agent OS is developed in explicit, reviewed phases. These rules apply
to every phase unless a specific phase's documentation says otherwise.

## Scope

- Keep changes scoped to the currently active phase. Do not bundle in work
  that belongs to a future phase, even if it feels convenient.
- Do not introduce speculative abstractions, configurability, or generality
  that isn't required by the current phase.
- Avoid generic `utils`, `helpers`, `common`, or `shared` directories/modules
  without a clear, specific owner and purpose.
- Do not mix unrelated changes into a single commit or pull request.

## Source Organization

- Domain logic goes in `src/friday/domain`, use cases in
  `src/friday/application`, adapters in `src/friday/infrastructure`.
  Deployable apps (`apps/api`, `apps/worker`, `apps/web`) stay thin
  composition roots — they wire lower layers together, they do not
  contain reusable domain or infrastructure logic.
- Language-neutral protocol definitions go in `packages/contracts`;
  the TypeScript client SDK goes in `packages/sdk-ts`. `sdk-ts` may
  depend on `contracts`; neither may depend on `apps/web`.
- A change to `tests/architecture/test_python_boundaries.py` or the
  TypeScript workspace boundary rules must be accompanied by a passing
  (and, for a rule change, a deliberately-failing-then-fixed) test run
  demonstrating the new rule is actually enforced — do not loosen a
  boundary rule without evidence it still catches a violation.

## Commits

- Use conventional commit-style messages: `type: description`
  (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`).
- Keep commits small and reviewable — one logical change per commit.

## Third-Party Code

- Document any imported or adapted third-party code at the point it is
  introduced (upstream repo, commit, original path, license, modifications).
- Do not copy source from other repositories without confirming compatible
  licensing and recording provenance. See
  [docs/governance/provenance.md](docs/governance/provenance.md).

## Documentation

- Update relevant documentation in the same change that introduces an
  architectural or structural shift — don't let docs drift from the code.

## Phase Transitions

- Each phase requires review before the next phase begins. Do not start the
  next phase's work inside a change intended for the current phase.

## Local Development

- Bootstrap: `just bootstrap`. Validate: `just check`.
- Lockfiles (`uv.lock`, `pnpm-lock.yaml`) are committed and must stay in
  sync — re-run `just bootstrap` after changing a manifest and commit the
  resulting lockfile diff.
- Adding a dependency requires a stated purpose in the commit/PR
  description and must go in the dev-dependency group unless a later
  phase specifically requires it as a production dependency.
- Tooling changes (lint rules, formatting config, CI-equivalent scripts)
  must include the validation command output (or a summary of it) in the
  PR description.

## Current Limitations

This repository is at the Phase 2 (clean source organization) stage.
There is no business logic, framework code, database, or AI integration —
only structure, dependency boundaries, and static composition-root
shells. Later phases will extend this document as real behavior is
introduced.

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

## Current Limitations

This repository is at the Phase 0 (repository foundation) stage. There is no
build, test, lint, or run tooling yet — none of that is documented here
because none of it exists. Later phases will extend this document as real
tooling is introduced.

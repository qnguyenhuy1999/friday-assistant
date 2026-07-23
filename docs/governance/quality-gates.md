# Quality Gates

This document describes the Phase 3 quality gates: what each one checks, the
exact command that runs it, where it runs (local/pre-commit/pre-push/CI), and
how to change it safely. It complements
[repository-rules.md](repository-rules.md) and
[provenance.md](provenance.md), which describe the policies these gates
enforce; this document describes the enforcement mechanism itself.

Every gate below is a `just` recipe. Pre-commit hooks and CI both invoke
those same recipes — see `.pre-commit-config.yaml` and
`.github/workflows/quality.yml` — so there is exactly one place each rule is
implemented.

## Formatting

- **Purpose:** consistent code style with no manual bikeshedding.
- **Implementation:** `ruff format` (Python) and `prettier` (JSON/YAML/TS).
- **Command:** `just format-check` (non-mutating) / `just format` (mutating).
- **Runs:** local, pre-commit (default stage), CI (`just check`).

## Linting

- **Purpose:** catch bugs, style violations, and unused code; enforce
  Markdown style.
- **Implementation:** `ruff check` (Python), `eslint` (TypeScript),
  `markdownlint-cli2` (Markdown, config in `.markdownlint-cli2.jsonc`;
  `docs/superpowers/plans/**` is excluded because those are auto-generated
  planning documents with their own checkbox/fenced-code layout, not
  hand-authored prose).
- **Command:** `just lint`.
- **Runs:** local, pre-commit (default stage), CI (`just check`).

## Shell Validation

- **Purpose:** catch shell scripting bugs in `scripts/*.sh`.
- **Implementation:** ShellCheck, via the `shellcheck-py` dev dependency
  (bundles the real ShellCheck binary, pinned through `uv.lock` — no system
  install required).
- **Command:** `just shellcheck`.
- **Runs:** local, pre-commit (default stage), CI (`just check`).

## Static Typing

- **Purpose:** catch type errors before runtime.
- **Implementation:** `mypy --strict` (Python), `tsc --noEmit` via each
  package's `tsconfig.typecheck.json` (TypeScript).
- **Command:** `just typecheck`.
- **Runs:** local, pre-commit (**pre-push** stage — slower than the
  commit-time gates), CI (`just check`).

## Tests

- **Purpose:** verify behavior.
- **Implementation:** `pytest`, covering `tests/architecture`,
  `tests/policy`, and `tests/toolchain`.
- **Command:** `just test` (all tests), `just architecture-check` (only
  `tests/architecture`), `just policy-check` (only `tests/policy`).
- **Runs:** local, pre-commit (`architecture-check` and `policy-check` run
  individually at the default/commit stage; the full `test` recipe runs as
  part of CI's `just check`).
- **Known overlap:** `architecture-check` and `policy-check` are subsets
  already exercised by `test`. They're kept as separate fast commit-time
  hooks so a contributor gets an explicit, immediately-failing signal naming
  exactly which policy dimension broke, at negligible added cost (each
  subset runs in well under a second). This is intentional, not a bug — see
  the comment above `check:` in the `justfile`.

## Coverage

- **Purpose:** prevent untested project code from silently accumulating.
- **Implementation:** `pytest-cov`, configured in `pyproject.toml`
  (`[tool.coverage.run]` / `[tool.coverage.report]`). Measures only
  `src/friday`, `apps/api`, `apps/worker` — tests are never counted.
  Branch coverage is enabled. Threshold is 90%, enforced via
  `fail_under` alone (verified to work with no `--cov-fail-under` CLI flag
  needed — see `tests/policy/test_coverage_enforcement.py`, which proves the
  mechanism against a synthetic under-covered module in a temp directory).
  The only exclusion is `if __name__ == "__main__":` (via `exclude_also`),
  justified because that line is a process entry-point guard exercised only
  by direct invocation, not unit tests — not a blanket `pragma: no cover`.
- **Command:** `just test-cov`.
- **Runs:** local, pre-commit (**pre-push** stage), CI.

## Python Architecture

- **Purpose:** enforce `domain <- application <- infrastructure` import
  direction.
- **Implementation:** `tests/architecture/test_python_boundaries.py`
  (`ast`-based import scan) and
  `tests/policy/test_repository_policy.py::test_domain_layers_contain_only_init_modules`
  (structural proxy for "no real domain/contract model exists yet").
- **Command:** `just architecture-check` (boundaries) /
  `just policy-check` (structural proxy).
- **Runs:** local, pre-commit (default stage), CI.

## TypeScript Architecture

- **Purpose:** enforce the `@friday/sdk` → `@friday/contracts` dependency
  direction and prevent any package from depending on `@friday/web`.
- **Implementation:** `tests/architecture/test_typescript_boundaries.py`
  (manifest-based check, since `tsc`'s project-reference graph won't fail on
  an unused disallowed manifest dependency).
- **Command:** `just architecture-check`.
- **Runs:** local, pre-commit (default stage), CI.

## Dependency Policy

- **Purpose:** keep Phase 3 dependency-free of production/runtime
  frameworks, dev-only, and free of wildcard/prerelease/direct-URL
  specifiers.
- **Implementation:**
  `tests/policy/test_python_dependency_policy.py` (parses `pyproject.toml`
  with `tomllib`) and `tests/policy/test_node_dependency_policy.py` (parses
  every workspace `package.json` with `json`).
- **Command:** `just policy-check`.
- **Runs:** local, pre-commit (default stage), CI.

## Repository Policy

- **Purpose:** keep the tracked tree free of generated artifacts, conflict
  markers, unexpected executables, and missing governance/ownership docs.
- **Implementation:** `tests/policy/test_repository_policy.py`, driven by
  `git ls-files` (so it reflects tracked state, not local untracked files).
- **Command:** `just policy-check`.
- **Runs:** local, pre-commit (default stage), CI.

## Provenance Policy

- **Purpose:** make the [provenance.md](provenance.md) policy executable —
  no vendored Javis/Hermes/Graphify source path is tracked without a
  matching documented mention, and no second license file appears
  undocumented.
- **Implementation:** `tests/policy/test_provenance.py`. No
  `provenance.yaml` registry exists yet — Phase 3 keeps the Markdown policy
  and validates repository state directly, since introducing a structured,
  zero-entry registry with no automated consumer would be a meaningless
  placeholder. Introduce one in a later phase only once something actually
  needs to read it programmatically.
- **Command:** `just policy-check`.
- **Runs:** local, pre-commit (default stage), CI.

## Sensitive Files

- **Purpose:** defense in depth against committed secrets, alongside the
  pre-commit `detect-private-key` hook.
- **Implementation:** `tests/policy/test_sensitive_files.py` — checks
  `.env` isn't tracked and is gitignored, no `id_rsa`/`*.pem`/`*.key`-shaped
  filenames are tracked, and no tracked file's contents match a PEM
  private-key header. `docs/superpowers/plans/**` is excluded from the
  content scan because planning documents show that same fixture as
  illustrative example text.
- **Command:** `just policy-check` (custom checks) plus the pre-commit
  `detect-private-key` hook (industry-standard heuristic scanner). The
  `detect-private-key` hook excludes `tests/policy/test_sensitive_files.py`
  and `docs/superpowers/plans/**` for the same reason — both intentionally
  contain private-key-shaped fixture text that isn't a real secret.
- **Runs:** local, pre-commit (default stage), CI.

## Markdown Links

- **Purpose:** catch broken relative links in documentation.
- **Implementation:** `tests/policy/test_markdown_links.py` — regex-scans
  every tracked `.md` file's Markdown-style links, skips absolute
  `http(s)://`/`mailto:`/anchor-only links, and resolves the rest relative
  to the linking file. Runs fully offline. `docs/superpowers/plans/**` is
  excluded (same reasoning as above: illustrative link syntax in code
  fences, not real prose links).
- **Command:** `just policy-check`.
- **Runs:** local, pre-commit (default stage), CI.

## Lockfile Reproducibility

- **Purpose:** guarantee `uv.lock` and `pnpm-lock.yaml` are exactly what a
  frozen install produces — no silent manifest/lockfile drift.
- **Implementation:** `scripts/lock_check.py` — hashes both lockfiles, runs
  `uv sync --locked` and `pnpm install --frozen-lockfile` (each already
  refuses to silently rewrite its lockfile), then re-hashes and diffs as
  defense in depth. Never resets a changed lockfile itself. The pure
  comparison function is unit-tested in `tests/policy/test_lock_check.py`
  without shelling out to real package managers.
- **Command:** `just lock-check`.
- **Runs:** local (manual), pre-commit (**pre-push** stage, since it
  performs real installs), CI (also serves as CI's actual frozen-install
  step).

## Changing a Policy Safely

Any change to a detector in `tests/policy/*.py` (or to
`tests/architecture/test_python_boundaries.py` / the TypeScript boundary
check) must be accompanied by a red-then-green run: temporarily break the
rule with a synthetic fixture, show the test fails, then restore it and show
the test passes. Every detector in this repository already includes such a
negative-fixture test (e.g. `test_detector_flags_...`) — extend that test
rather than adding a parallel one. See `CONTRIBUTING.md`.

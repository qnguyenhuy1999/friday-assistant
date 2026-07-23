# Phase 1 — Toolchain & Workspace Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the repository a reproducible Python (uv) + Node/pnpm dev toolchain — locked deps, lint/format/typecheck/test commands via a `justfile`, bootstrap/check scripts — with zero application or production code.

**Architecture:** Root-level `pyproject.toml` (uv, no build backend — `package = false`) hosts Python dev tooling against a `tests/` smoke suite. Root-level `package.json` (pnpm workspace, private) hosts Node dev tooling against a single `scripts/smoke.ts` file. A `justfile` is the single command surface; `scripts/bootstrap.sh` and `scripts/check.sh` are thin, non-duplicating wrappers around it.

**Tech Stack:** Python 3.14 + uv 0.10.12 + ruff 0.15.22 + mypy 2.3.0 + pytest 9.1.1 + pytest-cov 7.1.0. Node 22.23.1 (LTS "Jod") + pnpm 11.16.0 (via Corepack) + TypeScript 7.0.2 + ESLint 10.7.0 (flat config, via `typescript-eslint` 8.65.0 + `@eslint/js` 10.0.1) + Prettier 3.9.6. `just` (system tool, not package-manager-installed).

## Global Constraints

- Implement Phase 1 only — no FastAPI, Uvicorn, React, Vite, SQLAlchemy, Alembic, SQLite, domain models, API routes, worker code, Claude/Graphify/Obsidian/Hermes/computer-use code, Docker, or CI workflows.
- Zero production dependencies in both `pyproject.toml` and `package.json` — dev-only.
- No application source directories (no `apps/`, `packages/`, `src/`). `pnpm-workspace.yaml` may *declare* future `apps/*`/`packages/*` patterns without creating them.
- No placeholder/empty directories.
- Every created file has an immediate Phase 1 purpose — no speculative config.
- Prefer current stable, mutually compatible tool versions (verified against PyPI/npm registries and `uv python list` on 2026-07-23); no beta/RC/nightly.
- Commit lockfiles (`uv.lock`, `pnpm-lock.yaml`); conventional commit messages (`type: description`); small reviewable commits; no `--no-verify`/force-push.
- `.gitignore` and `.gitattributes` already exist from Phase 0 and already cover `.venv/`, `node_modules/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/`, `.eslintcache`, `*.py`/`*.ts`/`*.json`/`*.yaml` text normalization — do not modify them unless a real gap is found.
- `.editorconfig` already sets 2-space default indent, 4-space for `*.py` — new files must follow it (no reformatting existing files).

---

### Task 1: Python toolchain (uv, ruff, mypy, pytest, pytest-cov)

**Files:**
- Create: `.python-version`
- Create: `pyproject.toml`
- Create: `tests/test_toolchain_smoke.py`
- Generate: `uv.lock` (via `uv sync`, not hand-written)

**Interfaces:**
- Produces: `uv run ruff format .`, `uv run ruff check .`, `uv run mypy`, `uv run pytest`, `uv run pytest --cov=tests --cov-report=term-missing` — all runnable from repo root. Task 3's `justfile` recipes call these exact commands.

- [ ] **Step 1: Write `.python-version`**

```text
3.14
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "friday-agent-os"
version = "0.0.0"
description = "Local-first engineering agent operating system."
readme = "README.md"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = [
    "mypy>=2.3.0",
    "pytest>=9.1.1",
    "pytest-cov>=7.1.0",
    "ruff>=0.15.22",
]

[tool.uv]
package = false

[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.14"
strict = true
files = ["tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.coverage.run]
source = ["tests"]
```

`[tool.uv] package = false` marks this a non-package ("virtual") project — required because there is intentionally no importable Python package yet, so no build backend (e.g. hatchling) is needed. `files = ["tests"]` keeps mypy scoped to project-owned files, per spec (do not point it at nonexistent future packages).

- [ ] **Step 3: Write the smoke test**

```python
import sys
import tomllib
from pathlib import Path
from typing import Any


def _load_pyproject() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_python_version_matches_pyproject_requirement() -> None:
    data = _load_pyproject()
    requires_python: str = data["project"]["requires-python"]
    assert requires_python.startswith(">=3.14")
    assert sys.version_info >= (3, 14)


def test_repository_root_contains_expected_toolchain_files() -> None:
    root = Path(__file__).resolve().parent.parent
    expected = ("pyproject.toml", "package.json", "pnpm-workspace.yaml", "justfile")
    for name in expected:
        assert (root / name).is_file(), f"missing {name}"
```

Save as `tests/test_toolchain_smoke.py`.

- [ ] **Step 4: Sync and verify**

Run: `uv sync`
Expected: creates `.venv/` and `uv.lock`, installs ruff/mypy/pytest/pytest-cov, exits 0.

Run: `uv run pytest -v`
Expected: 2 passed.

Run: `uv run ruff format --check .` then `uv run ruff check .` then `uv run mypy`
Expected: all exit 0 with no findings.

- [ ] **Step 5: Commit**

```bash
git add .python-version pyproject.toml uv.lock tests/test_toolchain_smoke.py
git commit -m "chore: configure python development toolchain"
```

---

### Task 2: Node.js and pnpm workspace toolchain

**Files:**
- Create: `.node-version`
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `tsconfig.json`
- Create: `eslint.config.js`
- Create: `.prettierignore`
- Create: `scripts/smoke.ts`
- Generate: `pnpm-lock.yaml` (via `pnpm install`, not hand-written)

**Interfaces:**
- Consumes: none from Task 1.
- Produces: `pnpm exec tsc --noEmit`, `pnpm exec eslint scripts eslint.config.js`, `pnpm exec prettier --check/--write <globs>` — all runnable from repo root. Task 3's `justfile` recipes call these exact commands.

- [ ] **Step 1: Write `.node-version`**

```text
22.23.1
```

- [ ] **Step 2: Write `package.json`**

```json
{
  "name": "friday-agent-os",
  "version": "0.0.0",
  "private": true,
  "packageManager": "pnpm@11.16.0",
  "engines": {
    "node": "^20.19.0 || ^22.13.0 || >=24"
  },
  "scripts": {
    "format": "prettier --write \"scripts/**/*.ts\" \"*.json\" \"*.yaml\" \"*.yml\" eslint.config.js",
    "format:check": "prettier --check \"scripts/**/*.ts\" \"*.json\" \"*.yaml\" \"*.yml\" eslint.config.js",
    "lint": "eslint scripts eslint.config.js",
    "typecheck": "tsc --noEmit"
  },
  "devDependencies": {
    "@eslint/js": "^10.0.1",
    "eslint": "^10.7.0",
    "prettier": "^3.9.6",
    "typescript": "^7.0.2",
    "typescript-eslint": "^8.65.0"
  }
}
```

The `engines.node` range is the intersection of ESLint 10's and `typescript-eslint` 8's own `engines` requirements — it is the strictest constraint among our chosen deps, and the installed `22.23.1` satisfies it.

- [ ] **Step 3: Write `pnpm-workspace.yaml`**

```yaml
packages:
  - "apps/*"
  - "packages/*"
```

These directories are declared for future phases only — do not create them now.

- [ ] **Step 4: Write `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "lib": ["ES2022"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitReturns": true,
    "noFallthroughCasesInSwitch": true,
    "forceConsistentCasingInFileNames": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "noEmit": true
  },
  "include": ["scripts/**/*.ts"]
}
```

- [ ] **Step 5: Write `eslint.config.js`**

```js
const js = require("@eslint/js");
const tseslint = require("typescript-eslint");

module.exports = tseslint.config(
  {
    ignores: ["node_modules/**", "pnpm-lock.yaml"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["eslint.config.js"],
    languageOptions: {
      sourceType: "commonjs",
      globals: {
        require: "readonly",
        module: "writable",
      },
    },
  },
);
```

No type-aware linting (`parserOptions.project`) and no `globals` package — not needed for a single dependency-free smoke file, kept minimal per YAGNI.

- [ ] **Step 6: Write `.prettierignore`**

```text
pnpm-lock.yaml
node_modules/
.venv/
htmlcov/
```

Prevents Prettier from ever touching the pnpm lockfile even though `*.yaml` is in its target globs.

- [ ] **Step 7: Write `scripts/smoke.ts`**

```typescript
type SemVer = {
  readonly major: number;
  readonly minor: number;
  readonly patch: number;
};

function parseSemVer(version: string): SemVer {
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(version);
  if (!match) {
    throw new Error(`invalid semantic version: ${version}`);
  }
  const [, major, minor, patch] = match;
  return { major: Number(major), minor: Number(minor), patch: Number(patch) };
}

function isAtLeast(version: SemVer, minimum: SemVer): boolean {
  if (version.major !== minimum.major) return version.major > minimum.major;
  if (version.minor !== minimum.minor) return version.minor > minimum.minor;
  return version.patch >= minimum.patch;
}

const declaredNodeVersion = parseSemVer("22.23.1");
const minimumSupportedNode = parseSemVer("20.19.0");

if (!isAtLeast(declaredNodeVersion, minimumSupportedNode)) {
  throw new Error("toolchain smoke check failed: declared Node version below supported minimum");
}
```

Deliberately avoids any Node global (`process`, `fs`) so no `@types/node` dependency is needed. Exercises strict-mode features (regex-match null narrowing, readonly object types, tuple destructuring) that only compile cleanly under `strict: true` — this is what actually proves the TS toolchain, not the semver logic itself.

- [ ] **Step 8: Install and verify**

Run: `corepack enable && corepack prepare --activate`
Expected: activates pnpm 11.16.0.

Run: `pnpm install`
Expected: creates `node_modules/` and `pnpm-lock.yaml`, exits 0.

Run: `pnpm exec tsc --noEmit`
Expected: exits 0, no errors.

Run: `pnpm exec eslint scripts eslint.config.js`
Expected: exits 0, no findings.

Run: `pnpm exec prettier --check "scripts/**/*.ts" "*.json" "*.yaml" "*.yml" eslint.config.js`
Expected: exits 0 ("All matched files use Prettier code style!"). If not, run the `format` script once and re-check.

- [ ] **Step 9: Commit**

```bash
git add .node-version package.json pnpm-workspace.yaml pnpm-lock.yaml tsconfig.json eslint.config.js .prettierignore scripts/smoke.ts
git commit -m "chore: configure node and pnpm workspace"
```

---

### Task 3: `justfile` command interface

**Files:**
- Create: `justfile`

**Interfaces:**
- Consumes: exact commands produced by Task 1 (`uv run ...`) and Task 2 (`pnpm exec ...`).
- Produces: `just bootstrap|format|format-check|lint|typecheck|test|test-cov|check|clean`, consumed by Task 4's `scripts/check.sh`.

- [ ] **Step 1: Write `justfile`**

```just
set shell := ["bash", "-euo", "pipefail", "-c"]

bootstrap:
    ./scripts/bootstrap.sh

format:
    uv run ruff format .
    pnpm exec prettier --write "scripts/**/*.ts" "*.json" "*.yaml" "*.yml" eslint.config.js

format-check:
    uv run ruff format --check .
    pnpm exec prettier --check "scripts/**/*.ts" "*.json" "*.yaml" "*.yml" eslint.config.js

lint:
    uv run ruff check .
    pnpm exec eslint scripts eslint.config.js

typecheck:
    uv run mypy
    pnpm exec tsc --noEmit

test:
    uv run pytest

test-cov:
    uv run pytest --cov=tests --cov-report=term-missing

check: format-check lint typecheck test

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
```

`clean` only removes regenerable artifacts (venv, node_modules, caches, coverage output) — never lockfiles, source, docs, or `.git`.

- [ ] **Step 2: Verify each recipe**

Run: `just format-check && just lint && just typecheck && just test && just test-cov && just check`
Expected: all exit 0.

Run: `just clean`
Expected: removes `.venv/`, `node_modules/`, cache dirs; `git status --short` still shows no tracked-file changes.

Run: `just bootstrap` (re-populates what `clean` removed — see Task 4)
Expected: exits 0, `.venv/` and `node_modules/` restored.

- [ ] **Step 3: Commit**

```bash
git add justfile
git commit -m "chore: add repository bootstrap and validation commands"
```

---

### Task 4: Bootstrap and check scripts

**Files:**
- Create: `scripts/bootstrap.sh`
- Create: `scripts/check.sh`

**Interfaces:**
- Consumes: `just` recipes from Task 3 (`check.sh` calls `just check`); raw `uv`/`corepack`/`pnpm` commands (`bootstrap.sh` does not go through `just` since it must work before any recipe does).
- Produces: `./scripts/bootstrap.sh`, `./scripts/check.sh` — both runnable from any cwd, both idempotent.

- [ ] **Step 1: Write `scripts/bootstrap.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

require_command() {
  local name="$1"
  local hint="$2"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "error: required command '${name}' not found." >&2
    echo "  ${hint}" >&2
    exit 1
  fi
}

require_command uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
require_command node "Install Node.js 22 (LTS): https://nodejs.org/"
require_command corepack "Corepack ships with Node.js >= 16.9; if missing, upgrade Node."
require_command just "Install just: https://just.systems (e.g. 'brew install just')"

echo "== Toolchain versions =="
echo "python:   $(python3 --version)"
echo "uv:       $(uv --version)"
echo "node:     $(node --version)"
echo "corepack: $(corepack --version)"

echo "== Syncing Python dependencies (uv) =="
uv sync

echo "== Preparing pnpm via Corepack =="
corepack enable >/dev/null 2>&1 || true
corepack prepare --activate

echo "== Installing Node dependencies (pnpm, frozen lockfile) =="
pnpm install --frozen-lockfile

echo "pnpm:     $(pnpm --version)"
echo "Bootstrap complete."
```

- [ ] **Step 2: Write `scripts/check.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

if ! command -v just >/dev/null 2>&1; then
  echo "error: 'just' is required to run checks. Install: https://just.systems" >&2
  exit 1
fi

just check
```

`check.sh` intentionally contains no validation logic of its own — it delegates entirely to `just check` so the command set has one source of truth.

- [ ] **Step 3: Mark executable and verify**

Run: `chmod +x scripts/bootstrap.sh scripts/check.sh`

Run: `./scripts/bootstrap.sh`
Expected: prints versions, exits 0. Rerun immediately — must still exit 0 (idempotent).

Run: `./scripts/check.sh`
Expected: runs the same four checks as `just check`, exits 0.

Run: `cd / && /path/to/repo/scripts/check.sh; cd -`
Expected: still exits 0 (works from any cwd).

- [ ] **Step 4: Commit**

```bash
git add scripts/bootstrap.sh scripts/check.sh
git commit -m "chore: add repository bootstrap and validation commands"
```

(If Task 3's justfile commit already used this message, fold this into that same commit instead of creating a near-duplicate message — explain the choice in the final report.)

---

### Task 5: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: final command names from Tasks 1-4 (`just bootstrap`, `just check`, etc.) — must match exactly what was implemented.

- [ ] **Step 1: Add a development section to `README.md`**

Insert after the "## Documentation" section (before "## Development Status Disclaimer"):

```markdown
## Development

**Phase status:** Phase 1 — toolchain and workspace bootstrap only. No
application runtime exists yet (no API, no frontend, no database, no AI
integration).

Required runtimes:

- Python 3.14+ (managed via [uv](https://docs.astral.sh/uv/))
- Node.js 22.23.1+ (LTS "Jod") with Corepack enabled
- pnpm 11.16.0 (activated via Corepack from `packageManager` in
  `package.json`)
- [`just`](https://just.systems) as the local command runner

Bootstrap the toolchain:

\```bash
just bootstrap
\```

Run the full validation suite (format check, lint, typecheck, tests):

\```bash
just check
\```

See `justfile` for the complete list of available commands.
```

(Use literal triple-backtick fences, not the escaped ones shown here.)

- [ ] **Step 2: Update `CONTRIBUTING.md`**

Replace the "## Current Limitations" section (lines 40-45) with:

```markdown
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

This repository is at the Phase 1 (toolchain and workspace bootstrap)
stage. There is no application build, API, frontend, or database — none of
that is documented here because none of it exists.
```

- [ ] **Step 3: Verify links and prose**

Run: `grep -o '\[[^]]*\](\.[^)]*)' README.md CONTRIBUTING.md` and manually confirm every relative link still resolves to an existing file.

- [ ] **Step 4: Commit**

```bash
git add README.md CONTRIBUTING.md
git commit -m "docs: document local development workflow"
```

---

### Task 6: Full validation pass and final report

**Files:** none created — verification only.

- [ ] **Step 1: Run the complete required validation list from the spec section 13**, capturing exit status and relevant output for each:

```bash
git status --short
git diff --check
git ls-files

python3 --version
uv --version
node --version
corepack --version
pnpm --version

uv sync
pnpm install --frozen-lockfile

just format-check
just lint
just typecheck
just test
just test-cov
just check

scripts/bootstrap.sh
scripts/check.sh
```

- [ ] **Step 2: Reproducibility check** — run `uv sync` and `pnpm install --frozen-lockfile` a second time; confirm `git status --short` shows no diff in `uv.lock` or `pnpm-lock.yaml`.

- [ ] **Step 3: `just clean` safety check** — run `just clean`, then `git status --short`; confirm no tracked file is reported as deleted/modified. Re-run `just bootstrap` to restore.

- [ ] **Step 4: Produce the Mandatory Final Report** exactly per the spec's 18-section structure, using only commands that were actually executed in Steps 1-3.

---

## Self-Review Notes

- **Spec coverage:** Python toolchain (Task 1), Node/pnpm toolchain (Task 2), command interface (Task 3), bootstrap/check scripts (Task 4), docs (Task 5), full validation + report (Task 6) — covers spec sections 6-13 and 15. Section 14 (clean-room/provenance) is a reporting-only confirmation, addressed directly in the final report, not a file change.
- **No placeholders:** every step above has literal file contents, not descriptions.
- **Type/name consistency:** `just check` (Task 3) depends on recipe names `format-check`, `lint`, `typecheck`, `test` — matching exactly what Tasks 1-2 verified manually first. `scripts/check.sh` (Task 4) calls `just check`, not a re-implementation.

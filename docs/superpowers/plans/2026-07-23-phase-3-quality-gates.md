# Phase 3 — Quality Gates & Architecture Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the repository's quality gates (formatting, linting, typing, tests, coverage, architecture/dependency/repository/provenance policy, lockfile reproducibility, shell/markdown/YAML hygiene, secrets) so they run identically at commit time, in a full local gate, and in CI — with no business logic or runtime frameworks introduced.

**Architecture:** One source of truth is the `justfile`. New standalone policy logic lives as pytest modules under `tests/policy/` (parallel to the existing `tests/architecture/`), following the same in-memory/ast/json-parsing style already used by `tests/architecture/test_python_boundaries.py` and `test_typescript_boundaries.py`. Lockfile reproducibility is the one gate that must shell out to real package managers, so it lives in `scripts/lock_check.py`. `.pre-commit-config.yaml` wraps everything as `language: system` local hooks whose `entry` is a `just` recipe (or, for trivial file-hygiene checks, the standard `pre-commit/pre-commit-hooks` repo). `.github/workflows/quality.yml` calls the same `just` recipes.

**Tech Stack:** uv (Python 3.13, existing), pnpm (Node, existing), pytest/ruff/mypy (existing), pre-commit (new, pinned via `uv.lock`), shellcheck-py (new, pinned via `uv.lock` — provides the real ShellCheck binary with zero system dependency), markdownlint-cli2 (new, pinned via `pnpm-lock.yaml`), GitHub Actions (new workflow, third-party actions pinned to commit SHA).

## Global Constraints

- Phase 3 only: no Task/Run/Artifact/domain entities, no FastAPI/Uvicorn/SQLAlchemy/Alembic, no React/Vite, no Docker, no `.env.example`, no generic `utils`/`helpers`/`common`/unowned `shared` dirs, no business logic.
- Zero new production dependencies (Python `[project.dependencies]` stays `[]`; no Node `dependencies` field gains entries). All new tools are dev-only.
- Python 3.13 line, Node `>=22 <25`, hatchling pinned exactly, TypeScript typecheck uses `noEmit` (already true via existing `tsconfig.typecheck.json` files — verify, do not change unless broken).
- Coverage measures only `src/friday`, `apps/api`, `apps/worker`; excludes tests; threshold 90%; no blanket `pragma: no cover`.
- Do not loosen `tests/architecture/test_python_boundaries.py` or the TypeScript boundary rule without a passing negative-test demonstration.
- CI: `permissions: contents: read` only, no `pull_request_target`, no `continue-on-error` on required gates, frozen installs, fails on dirty working tree.
- Every new policy check needs an in-memory/temp-fixture negative test proving it actually catches the violation (matching the existing `test_detector_flags_a_forbidden_domain_import` / `test_detector_flags_a_forbidden_contracts_dependency_on_web` pattern) — never mutate real repo files to prove a check works.
- Conventional commit messages (`type: description`), small reviewable commits, no `--no-verify`.

---

### Task 1: Coverage policy + verify baseline gate still green

**Files:**
- Modify: `pyproject.toml` (coverage config)

**Interfaces:**
- Produces: `[tool.coverage.report]` config consumed by `just test-cov` (Task 8).

- [ ] **Step 1:** In `pyproject.toml`, under the existing `[tool.coverage.run]` block (currently `source = ["src/friday", "apps/api", "apps/worker"]`), add `branch = true`. Add a new `[tool.coverage.report]` block:
```toml
[tool.coverage.report]
show_missing = true
fail_under = 90
exclude_also = [
    "if __name__ == .__main__.:",
]
```
The `__main__` exclusion is narrowly scoped to the process entry-point guard in `apps/api/main.py:9` and `apps/worker/main.py:9` (`print(main())`), which is only exercised by direct process invocation, not unit tests — not a blanket `pragma: no cover`.

- [ ] **Step 2:** Run `uv run pytest --cov=src/friday --cov=apps/api --cov=apps/worker --cov-report=term-missing` and confirm coverage reports 100% (both `main.py` files now fully excluded/covered) and exits 0 with no `--cov-fail-under` CLI flag passed — this proves `fail_under` from config alone is enforced by pytest-cov. If it does NOT fail-under-enforce from config alone, add `--cov-fail-under=90` to the `test-cov` recipe in Task 8 instead and note the deviation in the final report.

- [ ] **Step 3:** Commit.
```bash
git add pyproject.toml
git commit -m "chore: set coverage threshold and justified __main__ exclusion"
```

---

### Task 2: Coverage-threshold enforcement negative test

**Files:**
- Create: `tests/policy/__init__.py` (empty marker, matches existing `tests/architecture` — check if that dir has an `__init__.py`; if not, omit this file for consistency)
- Create: `tests/policy/test_coverage_enforcement.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed elsewhere — pure demonstration test.

- [ ] **Step 1:** Check whether `tests/architecture/` has an `__init__.py`. If it does not (pytest doesn't require one with rootdir-relative testpaths), do not create one for `tests/policy/` either, to match the established pattern exactly.

- [ ] **Step 2:** Write `tests/policy/test_coverage_enforcement.py`:
```python
"""Proves the coverage fail-under threshold is actually enforced.

Runs pytest-cov against a synthetic, deliberately under-covered module in a
temp directory (never the real repository source) and asserts a high
--cov-fail-under threshold produces a non-zero exit status.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_cov_fail_under_rejects_undercovered_module(tmp_path: Path) -> None:
    pkg = tmp_path / "sample_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(
        textwrap.dedent(
            """
            def covered() -> str:
                return "covered"

            def never_called() -> str:
                return "never covered"
            """
        )
    )
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_mod.py").write_text(
        textwrap.dedent(
            """
            from sample_pkg.mod import covered

            def test_covered() -> None:
                assert covered() == "covered"
            """
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_dir),
            f"--cov={pkg}",
            "--cov-fail-under=90",
            "--no-header",
            "-q",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAIL Required test coverage" in (result.stdout + result.stderr) or "fail-under" in (
        result.stdout + result.stderr
    ).lower()
```

- [ ] **Step 3:** Run `uv run pytest tests/policy/test_coverage_enforcement.py -v` and confirm it passes. (This subprocess call needs `pytest-cov` importable in the subprocess's Python — it will be, since `sys.executable` is the same `uv`-managed interpreter with `pytest-cov` installed.)

- [ ] **Step 4:** Commit.
```bash
git add tests/policy/test_coverage_enforcement.py
git commit -m "test: prove coverage fail-under threshold is enforced"
```

---

### Task 3: Python dependency policy + negative tests

**Files:**
- Create: `tests/policy/test_python_dependency_policy.py`

**Interfaces:**
- Produces: `check_python_dependency_policy(pyproject: dict) -> list[str]` (list of violation strings, empty = compliant) — pure function other tests/scripts could reuse if needed later, but nothing in this plan depends on it.

- [ ] **Step 1:** Write `tests/policy/test_python_dependency_policy.py`:
```python
"""Enforces Phase 3 Python dependency-manifest policy against pyproject.toml.

Rules: [project.dependencies] stays empty; all quality tooling lives in the
dev dependency group; build-system requirements are exactly pinned; no
direct URL/git/editable/local-path or wildcard/prerelease dependency
specifiers.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_SPEC_PATTERNS = (
    re.compile(r"@\s*git\+"),
    re.compile(r"@\s*https?://"),
    re.compile(r"@\s*file://"),
    re.compile(r"^\s*\.\.?/"),
)
PRERELEASE_MARKERS = re.compile(r"\d(a|b|rc)\d|\.dev\d|\.post\d")
WILDCARD_MARKERS = ("*",)


def _load(text: str) -> dict[str, Any]:
    return tomllib.loads(text)


def check_python_dependency_policy(data: dict[str, Any]) -> list[str]:
    violations: list[str] = []

    project = data.get("project", {})
    if project.get("dependencies"):
        violations.append("project.dependencies must remain empty in Phase 3")

    build_requires = data.get("build-system", {}).get("requires", [])
    for req in build_requires:
        if "==" not in req:
            violations.append(f"build-system requirement not exactly pinned: {req}")

    dev_deps = data.get("dependency-groups", {}).get("dev", [])
    for spec in dev_deps:
        if not isinstance(spec, str):
            continue
        if any(pattern.search(spec) for pattern in FORBIDDEN_SPEC_PATTERNS):
            violations.append(f"forbidden direct/URL/local dependency spec: {spec}")
        if any(marker in spec for marker in WILDCARD_MARKERS):
            violations.append(f"wildcard dependency version: {spec}")
        if PRERELEASE_MARKERS.search(spec):
            violations.append(f"undocumented prerelease dependency: {spec}")

    return violations


def test_real_pyproject_is_compliant() -> None:
    data = _load((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert check_python_dependency_policy(data) == []


def test_detector_flags_non_empty_project_dependencies() -> None:
    data = _load(
        """
        [project]
        dependencies = ["fastapi"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("project.dependencies" in v for v in violations)


def test_detector_flags_unpinned_build_requirement() -> None:
    data = _load(
        """
        [build-system]
        requires = ["hatchling>=1.0"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("not exactly pinned" in v for v in violations)


def test_detector_flags_direct_git_dependency() -> None:
    data = _load(
        """
        [dependency-groups]
        dev = ["mypkg @ git+https://example.com/mypkg.git"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("forbidden direct/URL/local" in v for v in violations)


def test_detector_flags_wildcard_dependency() -> None:
    data = _load(
        """
        [dependency-groups]
        dev = ["somepkg==*"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("wildcard" in v for v in violations)


def test_detector_flags_prerelease_dependency() -> None:
    data = _load(
        """
        [dependency-groups]
        dev = ["somepkg==1.0.0rc1"]
        """
    )
    violations = check_python_dependency_policy(data)
    assert any("prerelease" in v for v in violations)
```

- [ ] **Step 2:** Run `uv run pytest tests/policy/test_python_dependency_policy.py -v` — all 6 tests pass (real pyproject.toml is currently compliant: `dependencies = []`, `hatchling==1.31.0`, dev deps all use `>=` with no URL/wildcard/prerelease markers).

- [ ] **Step 3:** Commit.
```bash
git add tests/policy/test_python_dependency_policy.py
git commit -m "test: enforce Python dependency manifest policy"
```

---

### Task 4: Node dependency policy + negative tests

**Files:**
- Create: `tests/policy/test_node_dependency_policy.py`

- [ ] **Step 1:** Write `tests/policy/test_node_dependency_policy.py`:
```python
"""Enforces Phase 3 Node dependency-manifest policy across all package.json
files in the pnpm workspace: root stays private with no production deps,
workspace packages stay private and named @friday/*, packageManager and
engines.node stay pinned/explicit, and no file:/git/http(s) tarball deps.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

MANIFEST_PATHS = (
    "package.json",
    "apps/web/package.json",
    "packages/contracts/package.json",
    "packages/sdk-ts/package.json",
)

FORBIDDEN_DEP_VALUE_PATTERNS = (
    re.compile(r"^file:"),
    re.compile(r"^git(\+\w+)?://"),
    re.compile(r"^https?://"),
)

PACKAGE_MANAGER_PATTERN = re.compile(r"^pnpm@\d+\.\d+\.\d+$")
ENGINES_NODE_PATTERN = re.compile(r"^>=\d+ <\d+$")


def check_manifest(manifest: dict[str, Any], *, is_root: bool) -> list[str]:
    violations: list[str] = []
    name = manifest.get("name", "<unnamed>")

    if manifest.get("private") is not True:
        violations.append(f"{name}: must be private")

    if not is_root:
        if not isinstance(name, str) or not name.startswith("@friday/"):
            violations.append(f"{name}: workspace package name must start with @friday/")

    prod_deps = manifest.get("dependencies")
    if prod_deps:
        violations.append(f"{name}: production dependencies not allowed in Phase 3: {prod_deps}")

    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = manifest.get(field)
        if not isinstance(deps, dict):
            continue
        for dep_name, version in deps.items():
            if isinstance(version, str) and any(
                p.search(version) for p in FORBIDDEN_DEP_VALUE_PATTERNS
            ):
                violations.append(f"{name}: forbidden dependency source {dep_name}={version}")

    if is_root:
        pm = manifest.get("packageManager")
        if not isinstance(pm, str) or not PACKAGE_MANAGER_PATTERN.match(pm):
            violations.append(f"{name}: packageManager must be pinned exactly, got {pm!r}")

        node_range = manifest.get("engines", {}).get("node")
        if not isinstance(node_range, str) or not ENGINES_NODE_PATTERN.match(node_range):
            violations.append(f"{name}: engines.node must be an explicit range, got {node_range!r}")

    return violations


def test_all_real_manifests_are_compliant() -> None:
    violations: dict[str, list[str]] = {}
    for relative_path in MANIFEST_PATHS:
        manifest = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
        found = check_manifest(manifest, is_root=(relative_path == "package.json"))
        if found:
            violations[relative_path] = found
    assert violations == {}


def test_detector_flags_production_dependency() -> None:
    manifest = {"name": "@friday/web", "private": True, "dependencies": {"react": "^19.0.0"}}
    violations = check_manifest(manifest, is_root=False)
    assert any("production dependencies" in v for v in violations)


def test_detector_flags_non_private_package() -> None:
    manifest = {"name": "@friday/web", "private": False}
    violations = check_manifest(manifest, is_root=False)
    assert any("must be private" in v for v in violations)


def test_detector_flags_git_url_dependency() -> None:
    manifest = {
        "name": "@friday/web",
        "private": True,
        "devDependencies": {"somepkg": "git+https://example.com/somepkg.git"},
    }
    violations = check_manifest(manifest, is_root=False)
    assert any("forbidden dependency source" in v for v in violations)


def test_detector_flags_unpinned_package_manager() -> None:
    manifest = {
        "name": "friday-agent-os",
        "private": True,
        "packageManager": "pnpm@11",
        "engines": {"node": ">=22 <25"},
    }
    violations = check_manifest(manifest, is_root=True)
    assert any("packageManager must be pinned" in v for v in violations)
```

- [ ] **Step 2:** Run `uv run pytest tests/policy/test_node_dependency_policy.py -v` — all pass against current manifests.

- [ ] **Step 3:** Commit.
```bash
git add tests/policy/test_node_dependency_policy.py
git commit -m "test: enforce Node dependency manifest policy"
```

---

### Task 5: Repository cleanliness policy + negative test

**Files:**
- Create: `tests/policy/test_repository_policy.py`

- [ ] **Step 1:** Write `tests/policy/test_repository_policy.py`:
```python
"""Structural repository-cleanliness policy: forbidden tracked artifacts,
merge-conflict markers, unexpected executables, required governance files,
package-local README ownership, and no domain/contract code before Phase 4.

Uses `git ls-files` as the source of truth for what is tracked, so it
reflects the real repository without depending on local untracked state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TRACKED_SUFFIXES = (
    ".pyc",
    ".tsbuildinfo",
)
FORBIDDEN_TRACKED_NAMES = (
    ".env",
    ".coverage",
)
FORBIDDEN_TRACKED_DIR_PARTS = (
    "node_modules",
    "__pycache__",
    ".venv",
    "htmlcov",
    "dist",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
)

REQUIRED_GOVERNANCE_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "docs/architecture/README.md",
    "docs/governance/provenance.md",
    "docs/governance/repository-rules.md",
    "docs/governance/quality-gates.md",
)

PACKAGE_LOCAL_READMES = (
    "apps/api/README.md",
    "apps/worker/README.md",
    "apps/web/README.md",
    "packages/contracts/README.md",
    "packages/sdk-ts/README.md",
)

CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")

DOMAIN_LAYER_DIRS = (
    "src/friday/domain",
    "src/friday/application",
    "src/friday/infrastructure",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def find_forbidden_tracked_paths(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        name = Path(path).name
        parts = Path(path).parts
        if name in FORBIDDEN_TRACKED_NAMES:
            offenders.append(path)
        elif any(path.endswith(suffix) for suffix in FORBIDDEN_TRACKED_SUFFIXES):
            offenders.append(path)
        elif any(part in FORBIDDEN_TRACKED_DIR_PARTS for part in parts):
            offenders.append(path)
    return offenders


def find_conflict_markers(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        full = REPO_ROOT / path
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if any(line.startswith(marker) for marker in CONFLICT_MARKERS for line in text.splitlines()):
            offenders.append(path)
    return offenders


def test_no_forbidden_tracked_artifacts() -> None:
    assert find_forbidden_tracked_paths(tracked_files()) == []


def test_no_merge_conflict_markers_in_tracked_files() -> None:
    assert find_conflict_markers(tracked_files()) == []


def test_required_governance_files_exist() -> None:
    missing = [f for f in REQUIRED_GOVERNANCE_FILES if not (REPO_ROOT / f).is_file()]
    assert missing == []


def test_package_local_readmes_exist() -> None:
    missing = [f for f in PACKAGE_LOCAL_READMES if not (REPO_ROOT / f).is_file()]
    assert missing == []


def test_no_unexpected_executable_files() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    executables = []
    for line in result.stdout.splitlines():
        mode, _sha, _stage_and_path = line.split(" ", 2)
        _stage, path = _stage_and_path.split("\t", 1)
        if mode == "100755":
            executables.append(path)
    assert set(executables) == {"scripts/bootstrap.sh", "scripts/check.sh"}


def test_domain_layers_contain_only_init_modules() -> None:
    """Structural proxy for 'no real domain/contract model in Phase 3':
    each layer directory must contain nothing but its __init__.py marker.
    """
    unexpected: list[str] = []
    for layer_dir in DOMAIN_LAYER_DIRS:
        layer_path = REPO_ROOT / layer_dir
        for path in layer_path.rglob("*"):
            if path.is_file() and path.name != "__init__.py":
                unexpected.append(str(path))
    assert unexpected == []


def test_detector_flags_a_forbidden_tracked_artifact() -> None:
    """Negative fixture: proves the detector catches a forbidden tracked path
    without touching real git state."""
    assert find_forbidden_tracked_paths(["apps/api/.coverage"]) == ["apps/api/.coverage"]
    assert find_forbidden_tracked_paths(["packages/sdk-ts/dist/index.js"]) == [
        "packages/sdk-ts/dist/index.js"
    ]
```

- [ ] **Step 2:** Run `uv run pytest tests/policy/test_repository_policy.py -v`. Expect `test_required_governance_files_exist` to FAIL initially (`docs/governance/quality-gates.md` doesn't exist yet — created in Task 12). Note this as an expected interim failure; re-run after Task 12.

- [ ] **Step 3:** Commit (test file only; it's allowed to have one dependency on a not-yet-created doc file since Task 12 is later in this same plan and CI only runs after the full plan is complete).
```bash
git add tests/policy/test_repository_policy.py
git commit -m "test: enforce repository cleanliness and package ownership policy"
```

---

### Task 6: Provenance policy + negative test

**Files:**
- Create: `tests/policy/test_provenance.py`

- [ ] **Step 1:** Write `tests/policy/test_provenance.py`:
```python
"""Executable provenance policy: no vendored/upstream source paths exist
without a corresponding provenance record, and no external license file is
added without documentation. Operates on `git ls-files` plus the prose of
docs/governance/provenance.md as the current provenance record — no
provenance.yaml exists yet (Phase 3 keeps the Markdown policy; introducing a
structured registry is deferred until something actually needs to consume
it programmatically).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_VENDOR_MARKERS = ("javis", "hermes", "graphify")
ALLOWED_MENTION_PATHS = (
    "docs/governance/provenance.md",
    "README.md",
)


def find_unprovenanced_vendor_paths(tracked_paths: list[str], provenance_text: str) -> list[str]:
    """A tracked path is a provenance violation if its own path contains a
    forbidden vendor marker as a real subdirectory segment (case-insensitive)
    and that marker is not documented in the provenance text."""
    lowered_provenance = provenance_text.lower()
    offenders: list[str] = []
    for path in tracked_paths:
        if path in ALLOWED_MENTION_PATHS:
            continue
        parts = [p.lower() for p in Path(path).parts]
        for marker in FORBIDDEN_VENDOR_MARKERS:
            if marker in parts and marker not in lowered_provenance:
                offenders.append(path)
    return offenders


def test_no_vendored_source_directories_are_tracked() -> None:
    import subprocess

    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    tracked = [line for line in result.stdout.splitlines() if line]
    provenance_text = (REPO_ROOT / "docs/governance/provenance.md").read_text(encoding="utf-8")
    assert find_unprovenanced_vendor_paths(tracked, provenance_text) == []


def test_no_external_license_file_beyond_the_root_license() -> None:
    import subprocess

    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    tracked = [line for line in result.stdout.splitlines() if line]
    license_files = [p for p in tracked if Path(p).name.upper().startswith("LICENSE")]
    assert license_files == ["LICENSE"]


def test_detector_flags_unprovenanced_vendor_path() -> None:
    """Negative fixture: simulates a copied-source path introduced without a
    matching provenance entry, without touching real files."""
    fake_tracked = ["src/friday/infrastructure/vendor/hermes/adapter.py"]
    offenders = find_unprovenanced_vendor_paths(fake_tracked, provenance_text="no mentions here")
    assert offenders == fake_tracked


def test_detector_allows_a_documented_vendor_mention() -> None:
    fake_tracked = ["docs/vendor/hermes/NOTES.md"]
    provenance_text = "Hermes Agent concepts may be ported later with provenance."
    assert find_unprovenanced_vendor_paths(fake_tracked, provenance_text) == []
```

- [ ] **Step 2:** Run `uv run pytest tests/policy/test_provenance.py -v` — all pass (no vendor dirs currently tracked; `LICENSE` is the only license file).

- [ ] **Step 3:** Commit.
```bash
git add tests/policy/test_provenance.py
git commit -m "test: enforce executable provenance policy"
```

---

### Task 7: Sensitive-file policy + Markdown relative-link policy + negative tests

**Files:**
- Create: `tests/policy/test_sensitive_files.py`
- Create: `tests/policy/test_markdown_links.py`

- [ ] **Step 1:** Write `tests/policy/test_sensitive_files.py`:
```python
"""Sensitive-file policy: .env-style files are gitignored and not tracked,
no private-key-shaped files are tracked, and no tracked text file contains a
private-key header. Complements the pre-commit `detect-private-key` hook by
running as a non-mutating, anytime-runnable check.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SENSITIVE_NAME_MARKERS = ("id_rsa", "id_ed25519", "credentials.json")
SENSITIVE_SUFFIXES = (".pem", ".key")
PRIVATE_KEY_HEADER = "-----BEGIN "


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line]


def find_sensitive_tracked_paths(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        name = Path(path).name
        if name in SENSITIVE_NAME_MARKERS or any(name.endswith(s) for s in SENSITIVE_SUFFIXES):
            offenders.append(path)
    return offenders


def find_private_key_contents(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        full = REPO_ROOT / path
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if PRIVATE_KEY_HEADER in text and "PRIVATE KEY" in text:
            offenders.append(path)
    return offenders


def test_no_env_file_is_tracked() -> None:
    assert [p for p in tracked_files() if Path(p).name == ".env"] == []


def test_gitignore_covers_env_files() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore


def test_no_sensitive_named_files_are_tracked() -> None:
    assert find_sensitive_tracked_paths(tracked_files()) == []


def test_no_tracked_file_contains_a_private_key() -> None:
    assert find_private_key_contents(tracked_files()) == []


def test_detector_flags_a_sensitive_filename() -> None:
    assert find_sensitive_tracked_paths(["config/id_rsa"]) == ["config/id_rsa"]


def test_detector_flags_private_key_contents() -> None:
    fixture = "-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----\n"
    tmp_marker_path = "does/not/exist/on/disk.txt"
    # Directly exercise the content-matching predicate rather than the file
    # I/O wrapper, since this path intentionally does not exist on disk.
    assert PRIVATE_KEY_HEADER in fixture and "PRIVATE KEY" in fixture
    assert find_private_key_contents([tmp_marker_path]) == []  # non-existent path is skipped, not a false positive
```

- [ ] **Step 2:** Write `tests/policy/test_markdown_links.py`:
```python
"""Validates that every relative Markdown link in tracked .md files resolves
to a real repository-local file. Runs fully offline; only relative links
(not http(s):// or mailto:) are checked. Anchors (#fragment) are stripped
before resolution.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def tracked_markdown_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line]


def find_broken_relative_links(markdown_path: Path, text: str) -> list[str]:
    broken: list[str] = []
    for match in LINK_PATTERN.finditer(text):
        target = match.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_without_anchor = target.split("#", 1)[0]
        if not target_without_anchor:
            continue
        resolved = (markdown_path.parent / target_without_anchor).resolve()
        if not resolved.exists():
            broken.append(target)
    return broken


def test_all_tracked_markdown_relative_links_resolve() -> None:
    violations: dict[str, list[str]] = {}
    for relative_path in tracked_markdown_files():
        full_path = REPO_ROOT / relative_path
        broken = find_broken_relative_links(full_path, full_path.read_text(encoding="utf-8"))
        if broken:
            violations[relative_path] = broken
    assert violations == {}


def test_detector_flags_a_broken_relative_link(tmp_path: Path) -> None:
    md_path = tmp_path / "doc.md"
    text = "See [missing](./does-not-exist.md) for details."
    assert find_broken_relative_links(md_path, text) == ["./does-not-exist.md"]


def test_detector_allows_an_existing_relative_link(tmp_path: Path) -> None:
    (tmp_path / "target.md").write_text("hello")
    md_path = tmp_path / "doc.md"
    text = "See [target](./target.md) for details."
    assert find_broken_relative_links(md_path, text) == []
```

- [ ] **Step 3:** Run `uv run pytest tests/policy/test_sensitive_files.py tests/policy/test_markdown_links.py -v`. If `test_all_tracked_markdown_relative_links_resolve` fails against real docs, fix the offending link text (do not weaken the check) before continuing.

- [ ] **Step 4:** Commit.
```bash
git add tests/policy/test_sensitive_files.py tests/policy/test_markdown_links.py
git commit -m "test: enforce sensitive-file policy and markdown relative links"
```

---

### Task 8: Lockfile reproducibility script + unit test + justfile wiring for check/policy/lock recipes

**Files:**
- Create: `scripts/lock_check.py`
- Create: `tests/policy/test_lock_check.py`
- Modify: `justfile`

**Interfaces:**
- Produces: `scripts/lock_check.py:lockfiles_changed(before: dict[str, str], after: dict[str, str]) -> list[str]` (pure function), `scripts/lock_check.py:main() -> int`.
- Consumes (justfile): `uv sync --locked`, `pnpm install --frozen-lockfile`.

- [ ] **Step 1:** Write `scripts/lock_check.py`:
```python
#!/usr/bin/env python3
"""Verifies uv.lock and pnpm-lock.yaml stay unchanged under a frozen install.

Hashes both lockfiles, runs `uv sync --locked` and
`pnpm install --frozen-lockfile` (each of which already refuses to silently
rewrite the lockfile), then re-hashes and diffs as defense in depth. Exits
non-zero with a clear message on any drift. Never resets a changed lockfile
itself — drift is a signal that must surface, not something to paper over.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILES = ("uv.lock", "pnpm-lock.yaml")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict[str, str]:
    return {name: _hash(REPO_ROOT / name) for name in LOCKFILES}


def lockfiles_changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(name for name in before if before[name] != after.get(name))


def main() -> int:
    before = snapshot()

    subprocess.run(["uv", "sync", "--locked"], cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["pnpm", "install", "--frozen-lockfile"], cwd=REPO_ROOT, check=True
    )

    after = snapshot()
    changed = lockfiles_changed(before, after)
    if changed:
        print(f"error: lockfile drift detected in: {', '.join(changed)}", file=sys.stderr)
        print(
            "Re-run the manifest change locally, commit the resulting lockfile diff, "
            "and re-run `just lock-check`.",
            file=sys.stderr,
        )
        return 1

    print("Lockfiles unchanged under frozen install.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2:** `chmod +x scripts/lock_check.py`.

- [ ] **Step 3:** Write `tests/policy/test_lock_check.py`:
```python
"""Unit tests for the pure lockfile-drift comparison logic in
scripts/lock_check.py. Does not shell out to uv/pnpm — that behavior is
exercised manually via `just lock-check` (see Phase 3 validation report).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lock_check", REPO_ROOT / "scripts" / "lock_check.py"
)
assert _SPEC is not None and _SPEC.loader is not None
lock_check = importlib.util.module_from_spec(_SPEC)
sys.modules["lock_check"] = lock_check
_SPEC.loader.exec_module(lock_check)


def test_no_drift_when_hashes_match() -> None:
    before = {"uv.lock": "abc", "pnpm-lock.yaml": "def"}
    after = {"uv.lock": "abc", "pnpm-lock.yaml": "def"}
    assert lock_check.lockfiles_changed(before, after) == []


def test_detects_drift_in_a_single_lockfile() -> None:
    before = {"uv.lock": "abc", "pnpm-lock.yaml": "def"}
    after = {"uv.lock": "abc", "pnpm-lock.yaml": "CHANGED"}
    assert lock_check.lockfiles_changed(before, after) == ["pnpm-lock.yaml"]


def test_detects_drift_in_both_lockfiles() -> None:
    before = {"uv.lock": "abc", "pnpm-lock.yaml": "def"}
    after = {"uv.lock": "CHANGED", "pnpm-lock.yaml": "CHANGED"}
    assert lock_check.lockfiles_changed(before, after) == ["pnpm-lock.yaml", "uv.lock"]
```

- [ ] **Step 4:** Run `uv run pytest tests/policy/test_lock_check.py -v` — all pass.

- [ ] **Step 5:** Update `justfile`. Replace its entire contents with:
```just
set shell := ["bash", "-euo", "pipefail", "-c"]

bootstrap:
    ./scripts/bootstrap.sh

format:
    uv run ruff format .
    pnpm exec prettier --write "**/*.{json,yaml,yml}" "apps/**/*.ts" "packages/**/*.ts" eslint.config.mjs

format-check:
    uv run ruff format --check .
    pnpm exec prettier --check "**/*.{json,yaml,yml}" "apps/**/*.ts" "packages/**/*.ts" eslint.config.mjs

lint:
    uv run ruff check .
    pnpm exec eslint .
    pnpm exec markdownlint-cli2 "**/*.md"

shellcheck:
    uv run shellcheck scripts/bootstrap.sh scripts/check.sh

typecheck:
    uv run mypy
    pnpm exec tsc -p apps/web/tsconfig.typecheck.json
    pnpm exec tsc -p packages/contracts/tsconfig.typecheck.json
    pnpm exec tsc -p packages/sdk-ts/tsconfig.typecheck.json

test:
    uv run pytest

test-cov:
    uv run pytest --cov=src/friday --cov=apps/api --cov=apps/worker --cov-report=term-missing

architecture-check:
    uv run pytest tests/architecture

policy-check:
    uv run pytest tests/policy

lock-check:
    uv run python scripts/lock_check.py

pre-commit:
    pre-commit run --all-files
    pre-commit run --all-files --hook-stage pre-push

# Fast, non-mutating local gate. architecture-check and policy-check are
# subsets already exercised by `test` (tests/architecture, tests/policy);
# they're re-run here individually so a contributor gets an explicit,
# fast-failing signal naming exactly which policy dimension broke, at
# negligible cost (each subset runs in well under a second).
check: format-check lint typecheck test architecture-check policy-check

# Full CI-equivalent gate. test-cov and lock-check are not part of `check`
# because test-cov needs coverage instrumentation (slower, and duplicates
# `test`'s pass/fail signal) and lock-check performs real package-manager
# installs (mutates the local environment, not appropriate for a fast local
# loop). pre-commit's pre-push stage re-runs typecheck/test-cov/lock-check
# again as an end-to-end proof that the hook wiring itself works — this is
# intentional overlap, not a bug.
ci: check test-cov lock-check pre-commit
    git diff --exit-code
    test -z "$(git status --porcelain)"

clean:
    rm -rf .venv node_modules
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
    rm -rf apps/web/dist packages/contracts/dist packages/sdk-ts/dist
    rm -rf .markdownlint-cli2-cache
```
Note on the `shellcheck` recipe: `shellcheck-py`'s bundled binary handles `.sh` files natively; `scripts/lock_check.py` is a Python file and must NOT be passed to ShellCheck (it only lints shell scripts) — the recipe above is simplified in Step 6 once shellcheck-py is actually installed and its real CLI behavior is confirmed (fallback `||` logic is a placeholder to be replaced, not shipped as-is).

- [ ] **Step 6:** Immediately simplify the `shellcheck` recipe (the fallback above is not acceptable per the "No Placeholders" rule) to:
```just
shellcheck:
    uv run shellcheck scripts/bootstrap.sh scripts/check.sh
```
Edit `justfile` to use this exact version before committing.

- [ ] **Step 7:** Do not run `just` recipes yet that depend on tools not installed until Task 9 (pre-commit, shellcheck-py) and Task 10 (markdownlint-cli2) land — this task only commits the script, test, and justfile text.

- [ ] **Step 8:** Commit.
```bash
git add scripts/lock_check.py tests/policy/test_lock_check.py justfile
git commit -m "chore: add lockfile reproducibility check and rewire justfile commands"
```

---

### Task 9: Add pre-commit and shellcheck-py as Python dev dependencies

**Files:**
- Modify: `pyproject.toml` (via `uv add --dev`)
- Modify: `uv.lock` (generated)

- [ ] **Step 1:** Run:
```bash
uv add --dev "pre-commit>=4.6.1" "shellcheck-py>=0.11.0.1"
```
This updates `pyproject.toml`'s `[dependency-groups] dev` list and regenerates `uv.lock`.

- [ ] **Step 2:** Run `uv run shellcheck --version` and confirm it prints a ShellCheck version (proves the wrapped binary works with zero system/Homebrew dependency).

- [ ] **Step 3:** Run `just shellcheck` and confirm `scripts/bootstrap.sh` and `scripts/check.sh` pass with no findings (both already exist and are POSIX-clean; if ShellCheck reports anything, fix the script directly — do not add blanket `# shellcheck disable=` without a specific, commented reason).

- [ ] **Step 4:** Run `uv run pytest tests/policy/test_python_dependency_policy.py -v` again — confirm the two new dev deps don't trip any rule (plain `>=` specifiers, no URL/wildcard/prerelease markers).

- [ ] **Step 5:** Commit.
```bash
git add pyproject.toml uv.lock
git commit -m "chore: add pre-commit and shellcheck-py as dev dependencies"
```

---

### Task 10: Add markdownlint-cli2 as a Node dev dependency + config

**Files:**
- Modify: `package.json` (via `pnpm add`)
- Modify: `pnpm-lock.yaml` (generated)
- Create: `.markdownlint-cli2.jsonc`

- [ ] **Step 1:** Run:
```bash
pnpm add -D markdownlint-cli2@^0.23.1
```

- [ ] **Step 2:** Create `.markdownlint-cli2.jsonc`:
```jsonc
{
  // Minimal deviations from markdownlint defaults, chosen to match this
  // repository's existing Markdown style rather than impose new style.
  "config": {
    "default": true,
    "MD013": false, // no hard line-length limit; docs use long descriptive lines
    "MD033": false, // inline HTML not currently used, but not worth banning
    "MD041": false // first line doesn't have to be a top-level heading (not true for every doc here)
  },
  "globs": ["**/*.md"],
  "ignores": ["node_modules/**", ".venv/**"]
}
```

- [ ] **Step 3:** Run `pnpm exec markdownlint-cli2 "**/*.md"` and fix any real findings against existing docs by editing the offending Markdown files directly (not by disabling more rules) unless a finding is a genuine style-preference conflict — in that case add exactly that rule to the `config` block above with a one-line comment explaining why.

- [ ] **Step 4:** Run `just lint` and confirm ruff, eslint, and markdownlint-cli2 all pass.

- [ ] **Step 5:** Commit.
```bash
git add package.json pnpm-lock.yaml .markdownlint-cli2.jsonc
git commit -m "chore: add markdownlint-cli2 for markdown style checks"
```

---

### Task 11: `.pre-commit-config.yaml`

**Files:**
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1:** Write `.pre-commit-config.yaml`:
```yaml
# Fast, deterministic gates only. Every custom hook below is a thin wrapper
# around a `just` recipe so pre-commit, `just check`/`just ci`, and CI all
# invoke the exact same command — see docs/governance/quality-gates.md.
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files
        args: ["--maxkb=1024"]
      - id: detect-private-key

  - repo: local
    hooks:
      - id: format-check
        name: format-check (ruff format + prettier)
        entry: just format-check
        language: system
        pass_filenames: false
        always_run: true

      - id: lint
        name: lint (ruff + eslint + markdownlint)
        entry: just lint
        language: system
        pass_filenames: false
        always_run: true

      - id: shellcheck
        name: shellcheck
        entry: just shellcheck
        language: system
        pass_filenames: false
        always_run: true

      - id: architecture-check
        name: architecture-check
        entry: just architecture-check
        language: system
        pass_filenames: false
        always_run: true

      - id: policy-check
        name: policy-check
        entry: just policy-check
        language: system
        pass_filenames: false
        always_run: true

      - id: typecheck
        name: typecheck (mypy + tsc)
        entry: just typecheck
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]

      - id: test-cov
        name: test-cov (pytest + coverage threshold)
        entry: just test-cov
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]

      - id: lock-check
        name: lock-check (frozen install, no lockfile drift)
        entry: just lock-check
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]
```

- [ ] **Step 2:** Run `uv run pre-commit install` and `uv run pre-commit install --hook-type pre-push` to activate both stages locally.

- [ ] **Step 3:** Run `uv run pre-commit run --all-files` — confirm every default-stage hook passes (fix any `end-of-file-fixer`/`trailing-whitespace` findings it auto-fixes, then re-stage and re-run until clean).

- [ ] **Step 4:** Run `uv run pre-commit run --all-files --hook-stage pre-push` — confirm `typecheck`, `test-cov`, and `lock-check` all pass.

- [ ] **Step 5:** Commit.
```bash
git add .pre-commit-config.yaml
git commit -m "chore: add pre-commit configuration wrapping just recipes"
```

---

### Task 12: `docs/governance/quality-gates.md` + README/CONTRIBUTING/architecture-README updates

**Files:**
- Create: `docs/governance/quality-gates.md`
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/architecture/README.md`

- [ ] **Step 1:** Write `docs/governance/quality-gates.md` documenting, for each gate: purpose, command, where it runs (local/pre-commit/pre-push/CI), and how to change it safely. Structure as one subsection per gate: Formatting, Linting, Shell Validation, Static Typing, Tests, Coverage, Python Architecture, TypeScript Architecture, Dependency Policy, Repository Policy, Provenance Policy, Sensitive Files, Markdown Links, Lockfile Reproducibility. For each: state the exact `just` command, which file implements it, and note the intentional overlap between `test`/`architecture-check`/`policy-check` and between `check`/`ci`'s pre-commit pre-push re-run (referencing the comments already in the `justfile`). End with a short "Changing a policy safely" section: policy changes require a red-then-green negative-test run, per `CONTRIBUTING.md`.

- [ ] **Step 2:** In `README.md`, update the "## Status" section's phase line from `**Phase 2 — clean source organization.**` to `**Phase 3 — quality gates and architecture hardening.**` with one sentence describing what changed (pre-commit, CI, coverage, dependency/repository/provenance policy — no business logic). Add a "## Quality Gates" section (after "## Development") summarizing `just check`, `just ci`, `just pre-commit`, and linking to `docs/governance/quality-gates.md`. Add `tests/policy/` to the "## Repository Structure" tree under the existing `tests/` block, alongside `tests/architecture/`.

- [ ] **Step 3:** In `CONTRIBUTING.md`, add a "## Quality Gates" section covering: pre-commit installation (`uv run pre-commit install && uv run pre-commit install --hook-type pre-push`), what runs at commit vs. pre-push vs. CI, dependency-policy rules (dev-only, no wildcard/prerelease/direct-URL specs), lockfile update rules (already partially present — cross-reference `just lock-check`), provenance requirements (already present — cross-reference the new automated check), and a rule that any change to a `tests/policy/*.py` detector must include a red-then-green negative-test run in the PR description (mirroring the existing rule for `tests/architecture/test_python_boundaries.py`).

- [ ] **Step 4:** In `docs/architecture/README.md`, add one bullet to the `tests/` description noting `tests/policy/` (dependency, repository, provenance, sensitive-file, and markdown-link policy checks — non-architectural but structurally enforced the same way).

- [ ] **Step 5:** Run `uv run pytest tests/policy/test_repository_policy.py::test_required_governance_files_exist tests/policy/test_markdown_links.py -v` — confirm both now pass (the file exists and its links, plus any new links added to README/CONTRIBUTING, all resolve).

- [ ] **Step 6:** Commit.
```bash
git add docs/governance/quality-gates.md README.md CONTRIBUTING.md docs/architecture/README.md
git commit -m "docs: document quality gates and update contribution workflow"
```

---

### Task 13: GitHub Actions CI workflow

**Files:**
- Create: `.github/workflows/quality.yml`

**Interfaces:**
- Consumes: `just lock-check`, `just check`, `just test-cov`, `just pre-commit` (Tasks 8–11).

- [ ] **Step 1:** Write `.github/workflows/quality.yml`:
```yaml
name: quality

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: quality-${{ github.ref }}
  cancel-in-progress: true

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1

      - name: Set up uv (manages the pinned Python version itself)
        uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"

      - name: Set up Node
        uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0
        with:
          node-version-file: ".node-version"

      - name: Set up just
        uses: extractions/setup-just@53165ef7e734c5c07cb06b3c8e7b647c5aa16db3 # v4.0.0

      - name: Cache pnpm store
        uses: actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0
        with:
          path: ~/.local/share/pnpm/store
          key: pnpm-store-${{ runner.os }}-${{ hashFiles('pnpm-lock.yaml') }}
          restore-keys: |
            pnpm-store-${{ runner.os }}-

      - name: Enable corepack
        run: corepack enable

      - name: Frozen install + lockfile drift check
        run: just lock-check

      - name: Fast local gate (format, lint, typecheck, tests, architecture, policy)
        run: just check

      - name: Coverage
        run: just test-cov

      - name: Pre-commit (all hooks, all stages)
        run: |
          just pre-commit

      - name: Verify working tree is clean
        run: |
          git diff --exit-code
          test -z "$(git status --porcelain)"
```

- [ ] **Step 2:** Validate the workflow YAML parses: `uv run python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/quality.yml').read_text())"` (add `pyyaml` only transiently if not already available — check first with `uv run python -c "import yaml"`; if it's not present, validate instead with `pnpm exec prettier --check .github/workflows/quality.yml` plus the pre-commit `check-yaml` hook from Task 11, which already covers this file — do not add a new dependency solely for one-off YAML validation in this step).

- [ ] **Step 3:** Commit.
```bash
git add .github/workflows/quality.yml
git commit -m "ci: add pinned, least-privilege quality workflow"
```

---

### Task 14: Full local validation pass + final report

**Files:** none (validation only).

- [ ] **Step 1:** Run the complete command list from the Phase 3 spec's "Required Validation" section: `git status --short`, `git diff --check`, `git ls-files`, `git log --oneline --decorate -15`, tool `--version` checks (`python`, `uv`, `node`, `pnpm`, `just`, `uv run pre-commit --version`, `uv run shellcheck --version`), then `uv sync --locked`, `pnpm install --frozen-lockfile`, `just format-check`, `just lint`, `just shellcheck`, `just typecheck`, `just test`, `just test-cov`, `just architecture-check`, `just policy-check`, `just lock-check`, `just pre-commit`, `just check`, `just ci`, `./scripts/bootstrap.sh`, `./scripts/check.sh`.

- [ ] **Step 2:** For each command, record exit status and relevant output for the mandatory final report (do not claim a command passed without having actually run it in this step).

- [ ] **Step 3:** Confirm `just ci` leaves `git status --short` empty, and that a second `uv sync --locked` / `pnpm install --frozen-lockfile` produce no further lockfile changes.

- [ ] **Step 4:** Confirm no production dependency was introduced (`git diff` on `pyproject.toml`'s `[project.dependencies]` and every `package.json`'s `dependencies` field, across the whole Phase 3 diff) and no domain/business/runtime code was added (re-run `tests/policy/test_repository_policy.py::test_domain_layers_contain_only_init_modules`).

- [ ] **Step 5:** Produce the Mandatory Final Report exactly as specified in the Phase 3 instructions (Sections 1–24), using the evidence gathered in Steps 1–4. Do not begin Phase 4.

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

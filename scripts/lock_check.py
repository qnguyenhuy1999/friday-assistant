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
    subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=REPO_ROOT, check=True)

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

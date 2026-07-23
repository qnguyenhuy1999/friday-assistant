"""Sensitive-file policy: .env-style files are gitignored and not tracked,
no private-key-shaped files are tracked, and no tracked text file contains a
private-key header. Complements the pre-commit `detect-private-key` hook by
running as a non-mutating, anytime-runnable check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Auto-generated planning documents contain illustrative code fixtures (this
# detector's own plan, for instance, shows the private-key test fixture as
# example text) rather than real content, so they're excluded from the
# content scan the same way they're excluded from markdownlint.
EXCLUDED_CONTENT_SCAN_PREFIXES = ("docs/superpowers/plans/",)

SENSITIVE_NAME_MARKERS = ("id_rsa", "id_ed25519", "credentials.json")
SENSITIVE_SUFFIXES = (".pem", ".key")

# A real PEM private-key header is "-----BEGIN <TYPE> PRIVATE KEY-----" as one
# contiguous line. Matching the full pattern (rather than checking for
# "-----BEGIN " and "PRIVATE KEY" as two independent substrings) means this
# detector's own source code never matches itself, since nowhere in this file
# does that exact contiguous text appear outside of a deliberately
# reconstructed test fixture (see test_detector_flags_private_key_contents).
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


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
        if path.startswith(EXCLUDED_CONTENT_SCAN_PREFIXES):
            continue
        full = REPO_ROOT / path
        if not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if PRIVATE_KEY_PATTERN.search(text):
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
    # Built from separate parts, not a single literal, so this test file's
    # own tracked source never contains the contiguous marker text it's
    # testing for — otherwise the real repository scan would flag itself.
    begin_marker = "-" * 5 + "BEGIN RSA PRIVATE KEY" + "-" * 5
    end_marker = "-" * 5 + "END RSA PRIVATE KEY" + "-" * 5
    fixture = f"{begin_marker}\nMIIB...\n{end_marker}\n"
    nonexistent_path = "does/not/exist/on/disk.txt"
    assert PRIVATE_KEY_PATTERN.search(fixture)
    # A path that doesn't exist on disk must be skipped, not treated as a
    # false positive.
    assert find_private_key_contents([nonexistent_path]) == []

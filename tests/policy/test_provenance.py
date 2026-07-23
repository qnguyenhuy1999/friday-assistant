"""Executable provenance policy: no vendored/upstream source paths exist
without a corresponding provenance record, and no external license file is
added without documentation. Operates on `git ls-files` plus the prose of
docs/governance/provenance.md as the current provenance record — no
provenance.yaml exists yet (Phase 3 keeps the Markdown policy; introducing a
structured registry is deferred until something actually needs to consume
it programmatically).
"""

from __future__ import annotations

import subprocess
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
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    tracked = [line for line in result.stdout.splitlines() if line]
    provenance_text = (REPO_ROOT / "docs/governance/provenance.md").read_text(encoding="utf-8")
    assert find_unprovenanced_vendor_paths(tracked, provenance_text) == []


def test_no_external_license_file_beyond_the_root_license() -> None:
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

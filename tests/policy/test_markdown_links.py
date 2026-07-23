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

# Auto-generated planning documents embed illustrative Markdown-link syntax
# inside fenced code blocks (as example content, not real prose links), so
# they're excluded here the same way they're excluded from markdownlint.
EXCLUDED_LINK_SCAN_PREFIXES = ("docs/superpowers/plans/",)

LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def tracked_markdown_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [
        line
        for line in result.stdout.splitlines()
        if line and not line.startswith(EXCLUDED_LINK_SCAN_PREFIXES)
    ]


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

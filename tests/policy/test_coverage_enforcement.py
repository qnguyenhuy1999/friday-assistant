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
    combined = (result.stdout + result.stderr).lower()
    assert "required test coverage" in combined or "fail" in combined

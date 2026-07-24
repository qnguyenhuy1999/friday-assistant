"""A fake Claude executable for worker composition tests.

Answers --version/--help probes (advertising every brain-only flag) and
otherwise behaves like the CLI in -p mode: reads the prompt from stdin and
prints the next scripted JSON envelope. The response counter persists on
disk, so consecutive worker claims (separate subprocesses) consume the
script in order."""

from __future__ import annotations

import json
import stat
from pathlib import Path

_TEMPLATE = """#!/usr/bin/env python3
import json, os, sys

RECORD = {record!r}
STDOUTS = {stdouts!r}
FLAGS = {flags!r}

if "--version" in sys.argv:
    print("9.9.9 (fake-claude)")
    sys.exit(0)
if "--help" in sys.argv:
    print("usage: fake-claude\\n" + "\\n".join(FLAGS))
    sys.exit(0)

data = sys.stdin.read()
count_path = os.path.join(RECORD, "count")
count = int(open(count_path).read()) if os.path.exists(count_path) else 0
open(count_path, "w").write(str(count + 1))
open(os.path.join(RECORD, f"stdin-{{count}}.txt"), "w").write(data)
sys.stdout.write(STDOUTS[min(count, len(STDOUTS) - 1)])
sys.exit(0)
"""

REQUIRED_FLAGS = (
    "--print",
    "--output-format",
    "--tools",
    "--safe-mode",
    "--strict-mcp-config",
    "--no-session-persistence",
    "--system-prompt",
)


def envelope(action_json: str) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": action_json})


def make_fake_claude(
    tmp_path: Path,
    *,
    action_jsons: list[str],
    flags: tuple[str, ...] = REQUIRED_FLAGS,
) -> tuple[str, Path]:
    """Returns (executable path, record dir). Each scripted action JSON is
    wrapped in a CLI result envelope and served once, in order."""
    record = tmp_path / "fake-claude-record"
    record.mkdir(exist_ok=True)
    script = tmp_path / "fake-claude"
    script.write_text(
        _TEMPLATE.format(
            record=str(record),
            stdouts=[envelope(action) for action in action_jsons],
            flags=list(flags),
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), record

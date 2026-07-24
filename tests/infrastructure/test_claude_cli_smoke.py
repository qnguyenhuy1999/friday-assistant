"""Manual smoke test against the real, locally authenticated Claude CLI.

Never runs in the default suite or CI: it requires FRIDAY_CLAUDE_SMOKE=1 and
a locally authenticated claude.ai subscription, and it performs one real
(paid) model call. Invoke explicitly:

    FRIDAY_CLAUDE_SMOKE=1 uv run pytest tests/infrastructure/test_claude_cli_smoke.py -q
"""

from __future__ import annotations

import os
import shutil

import pytest

from friday.application.brain_runtime import BrainRequest
from friday.application.runtime_actions import (
    FailAction,
    FinishAction,
    InvokeToolAction,
    YieldAction,
)
from friday.application.tool_gateway import ToolDescriptor
from friday.domain.identifiers import RunId, TaskId
from friday.infrastructure.brain.claude_cli import ClaudeCliBrainRuntime, ClaudeCliSettings

pytestmark = pytest.mark.skipif(
    os.environ.get("FRIDAY_CLAUDE_SMOKE") != "1",
    reason="real-Claude smoke test; set FRIDAY_CLAUDE_SMOKE=1 to run manually",
)


def test_real_claude_returns_one_valid_action() -> None:
    executable = shutil.which("claude")
    assert executable is not None, "claude executable not found on PATH"
    runtime = ClaudeCliBrainRuntime(
        ClaudeCliSettings(
            executable=executable,
            model="haiku",
            timeout_seconds=120.0,
            max_output_bytes=1_000_000,
        )
    )
    request = BrainRequest(
        run_id=RunId.new(),
        task_id=TaskId.new(),
        turn_number=1,
        attempt_number=1,
        context=(
            "# OBJECTIVE\nTask smoke-test: Nothing is left to do. "
            "Finish immediately with the summary 'smoke test ok'.\n\n"
            "# TOOLS\n- workspace.list (read-only, no approval): List entries."
        ),
        tool_manifest=(
            ToolDescriptor(
                name="workspace.list",
                description="List entries.",
                read_only=True,
                approval_required=False,
            ),
        ),
        max_response_bytes=65536,
    )
    response = runtime.next_action(request)
    assert isinstance(response.action, FinishAction | FailAction | YieldAction | InvokeToolAction)
    assert isinstance(response.action, FinishAction), response.action

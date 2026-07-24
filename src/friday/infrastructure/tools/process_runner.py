"""process.run — execute one command inside the workspace.

Argv-list only: there is no shell, no string command form, and no way to
express pipes, `&&`, or redirection. The child runs in its own session with
a workspace-confined cwd, an allowlisted environment, a hard timeout with
process-group SIGKILL, and byte caps on captured stdout/stderr. A non-zero
exit is a *successful* execution whose exit code is data; only failures of
the execution machinery itself (timeout, spawn failure) become Failures."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from friday.application.errors import ToolInputInvalid
from friday.application.tool_gateway import ToolExecutionResult
from friday.domain.failure import Failure, FailureCause
from friday.domain.json_value import JsonValue
from friday.infrastructure.tools.workspace_paths import resolve_workspace_path

PROCESS_ENVIRONMENT_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
"""Environment a tool subprocess may inherit — no FRIDAY_*, no ANTHROPIC_*,
no credentials."""

_MAX_ARGV_ITEMS = 64
_TRUNCATION_MARKER = "…[truncated]"


@dataclass(frozen=True, slots=True)
class ProcessRunnerSettings:
    root: Path
    timeout_seconds: float
    max_timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_timeout_seconds < self.timeout_seconds:
            raise ValueError("max_timeout_seconds must be >= timeout_seconds")
        if self.max_stdout_bytes <= 0 or self.max_stderr_bytes <= 0:
            raise ValueError("output caps must be positive")


class ProcessRunner:
    def __init__(self, settings: ProcessRunnerSettings) -> None:
        self._settings = settings

    def run(self, tool_input: JsonValue) -> ToolExecutionResult:
        argv, cwd, timeout = self._parse_input(tool_input)
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._environment(),
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except FileNotFoundError:
            return ToolExecutionResult.failed(
                Failure(
                    code="tool_execution_failed",
                    message=f"command not found: {argv[0]}",
                    retryable=False,
                    cause=FailureCause.TOOL,
                )
            )
        except OSError:
            return ToolExecutionResult.failed(
                Failure(
                    code="tool_execution_failed",
                    message="command could not be started",
                    retryable=False,
                    cause=FailureCause.TOOL,
                )
            )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            return ToolExecutionResult.failed(
                Failure(
                    code="tool_timeout",
                    message=f"command exceeded {timeout}s",
                    retryable=True,
                    cause=FailureCause.TIMEOUT,
                )
            )

        stdout, stdout_truncated = _cap(stdout, self._settings.max_stdout_bytes)
        stderr, stderr_truncated = _cap(stderr, self._settings.max_stderr_bytes)
        return ToolExecutionResult.succeeded(
            {
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        )

    def _parse_input(self, tool_input: JsonValue) -> tuple[list[str], Path, float]:
        if not isinstance(tool_input, dict):
            raise ToolInputInvalid("tool input must be a JSON object")
        unknown = set(tool_input) - {"argv", "cwd", "timeout_seconds"}
        if unknown:
            raise ToolInputInvalid(f"unknown input field(s): {sorted(unknown)}")

        argv_raw = tool_input.get("argv")
        if (
            not isinstance(argv_raw, list)
            or not argv_raw
            or len(argv_raw) > _MAX_ARGV_ITEMS
            or not all(isinstance(item, str) and item for item in argv_raw)
        ):
            raise ToolInputInvalid(
                "'argv' must be a non-empty list of non-empty strings"
                f" (at most {_MAX_ARGV_ITEMS} items)"
            )
        argv = [str(item) for item in argv_raw]

        cwd_raw = tool_input.get("cwd", ".")
        if not isinstance(cwd_raw, str) or not cwd_raw:
            raise ToolInputInvalid("'cwd' must be a non-empty string")
        cwd = resolve_workspace_path(self._settings.root, cwd_raw)
        if not cwd.is_dir():
            raise ToolInputInvalid(f"cwd is not a directory: {cwd_raw}")

        timeout_raw = tool_input.get("timeout_seconds", self._settings.timeout_seconds)
        if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, (int, float)):
            raise ToolInputInvalid("'timeout_seconds' must be a number")
        timeout = float(timeout_raw)
        if timeout <= 0 or timeout > self._settings.max_timeout_seconds:
            raise ToolInputInvalid(
                f"'timeout_seconds' must be in (0, {self._settings.max_timeout_seconds}]"
            )
        return argv, cwd, timeout

    def _environment(self) -> dict[str, str]:
        return {
            name: value
            for name in PROCESS_ENVIRONMENT_ALLOWLIST
            if (value := os.environ.get(name)) is not None
        }


def _cap(text: str, max_bytes: int) -> tuple[str, bool]:
    if len(text.encode("utf-8")) <= max_bytes:
        return text, False
    clipped = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return clipped + _TRUNCATION_MARKER, True


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race with exit
        process.kill()
    process.communicate()

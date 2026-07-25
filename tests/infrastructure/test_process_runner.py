"""process.run tests: argv-only execution (no shell), workspace-confined
cwd, allowlisted environment, timeout with process-group kill, output caps,
and exit-code capture as data."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from friday.application.errors import ToolInputInvalid, WorkspaceAccessDenied
from friday.domain.json_value import JsonValue
from friday.infrastructure.tools.process_runner import (
    PROCESS_ENVIRONMENT_ALLOWLIST,
    ProcessRunner,
    ProcessRunnerSettings,
)
from friday.infrastructure.tools.workspace_paths import resolve_workspace_root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sub").mkdir()
    return root


def runner(workspace: Path, **overrides: object) -> ProcessRunner:
    fields: dict[str, object] = {
        "root": resolve_workspace_root(workspace),
        "timeout_seconds": 10.0,
        "max_timeout_seconds": 30.0,
        "max_stdout_bytes": 10_000,
        "max_stderr_bytes": 10_000,
    }
    fields.update(overrides)
    return ProcessRunner(ProcessRunnerSettings(**fields))  # type: ignore[arg-type]


def py(code: str) -> list[JsonValue]:
    return [sys.executable, "-c", code]


def test_argv_execution_captures_stdout_and_exit_code(workspace: Path) -> None:
    result = runner(workspace).run({"argv": py("print('hi')")})
    assert result.status == "succeeded"
    assert isinstance(result.output, dict)
    assert result.output["exit_code"] == 0
    assert result.output["stdout"] == "hi\n"
    assert result.output["stderr"] == ""


def test_nonzero_exit_is_data_not_failure(workspace: Path) -> None:
    result = runner(workspace).run({"argv": py("import sys; sys.exit(3)")})
    assert result.status == "succeeded"
    assert isinstance(result.output, dict)
    assert result.output["exit_code"] == 3


def test_no_shell_interpretation(workspace: Path) -> None:
    # if a shell were involved, this would create the file; as argv it is
    # a single unknown command name
    result = runner(workspace).run({"argv": ["touch pwned.txt && echo done"]})
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "tool_execution_failed"
    assert not (workspace / "pwned.txt").exists()


def test_command_not_found_fails_structurally(workspace: Path) -> None:
    result = runner(workspace).run({"argv": ["definitely-not-a-command-xyz"]})
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "tool_execution_failed"


def test_cwd_defaults_to_workspace_root(workspace: Path) -> None:
    result = runner(workspace).run({"argv": py("import os; print(os.getcwd())")})
    assert isinstance(result.output, dict)
    stdout = result.output["stdout"]
    assert isinstance(stdout, str)
    assert Path(stdout.strip()) == workspace.resolve()


def test_cwd_inside_workspace_is_honored(workspace: Path) -> None:
    result = runner(workspace).run({"argv": py("import os; print(os.getcwd())"), "cwd": "sub"})
    assert isinstance(result.output, dict)
    stdout = result.output["stdout"]
    assert isinstance(stdout, str)
    assert Path(stdout.strip()) == (workspace / "sub").resolve()


def test_cwd_escape_is_denied(workspace: Path) -> None:
    with pytest.raises(WorkspaceAccessDenied):
        escape_input: JsonValue = {"argv": ["true"], "cwd": ".."}
        runner(workspace).run(escape_input)


def test_environment_is_allowlisted(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("FRIDAY_WORKER_DATABASE_URL", "sqlite:///x")
    result = runner(workspace).run(
        {"argv": py("import os, json; print(json.dumps(sorted(os.environ)))")}
    )
    assert isinstance(result.output, dict)
    stdout = result.output["stdout"]
    assert isinstance(stdout, str)
    assert "ANTHROPIC_API_KEY" not in stdout
    assert "FRIDAY_WORKER_DATABASE_URL" not in stdout
    import json as json_module

    child_env = set(json_module.loads(stdout))
    assert child_env <= set(PROCESS_ENVIRONMENT_ALLOWLIST) | {"__CF_USER_TEXT_ENCODING"}


def test_timeout_kills_process_tree(workspace: Path) -> None:
    code = (
        "import subprocess, sys, time;"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
        "open('child-pid.txt', 'w').write(str(child.pid));"
        "time.sleep(30)"
    )
    result = runner(workspace).run({"argv": py(code), "timeout_seconds": 1.5})
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "tool_timeout"
    assert result.failure.cause.value == "timeout"
    # the grandchild must be dead too (process-group SIGKILL)
    pid_file = workspace / "child-pid.txt"
    if pid_file.exists():
        import os
        import time

        child_pid = int(pid_file.read_text())
        time.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)


def test_stdout_cap_truncates_with_marker(workspace: Path) -> None:
    result = runner(workspace, max_stdout_bytes=50).run({"argv": py("print('x' * 500)")})
    assert isinstance(result.output, dict)
    assert result.output["stdout_truncated"] is True
    stdout = result.output["stdout"]
    assert isinstance(stdout, str)
    assert stdout.endswith("…[truncated]")


def test_stderr_cap_truncates_with_marker(workspace: Path) -> None:
    result = runner(workspace, max_stderr_bytes=50).run(
        {"argv": py("import sys; sys.stderr.write('e' * 500)")}
    )
    assert isinstance(result.output, dict)
    assert result.output["stderr_truncated"] is True


_INVALID_INPUTS: list[object] = [
    {"argv": []},
    {"argv": "echo hi"},
    {"argv": ["echo", ""]},
    {"argv": ["echo"], "timeout_seconds": 0},
    {"argv": ["echo"], "timeout_seconds": 9999},
    {"argv": ["echo"], "timeout_seconds": True},
    {"argv": ["echo"], "shell": True},
    {"argv": ["x"] * 65},
    "not an object",
]


@pytest.mark.parametrize("tool_input", _INVALID_INPUTS)
def test_invalid_inputs_are_rejected(workspace: Path, tool_input: object) -> None:
    with pytest.raises(ToolInputInvalid):
        runner(workspace).run(tool_input)  # type: ignore[arg-type]


def test_settings_invariants() -> None:
    with pytest.raises(ValueError):
        ProcessRunnerSettings(
            root=Path("."),
            timeout_seconds=0,
            max_timeout_seconds=1,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )
    with pytest.raises(ValueError):
        ProcessRunnerSettings(
            root=Path("."),
            timeout_seconds=10,
            max_timeout_seconds=5,
            max_stdout_bytes=1,
            max_stderr_bytes=1,
        )

"""Runtime budget settings fail closed for non-finite values."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.worker.runtime_settings import RuntimeSettings


def _settings(tmp_path: Path, **overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "workspace_root": tmp_path,
        "brain_backend": "claude_cli",
        "claude_executable": "claude",
        "claude_model": None,
        "claude_timeout_seconds": 30.0,
        "claude_max_output_bytes": 1_000_000,
        "max_turns_per_claim": 8,
        "max_tool_calls_per_claim": 4,
        "max_context_chars": 60_000,
        "max_response_bytes": 65_536,
        "max_yield_seconds": 3_600,
        "tool_timeout_seconds": 10.0,
        "tool_max_timeout_seconds": 30.0,
        "tool_max_stdout_bytes": 100_000,
        "tool_max_stderr_bytes": 100_000,
        "tool_max_file_bytes": 1_000_000,
        "tool_max_list_entries": 100,
        "max_processing_seconds": 600.0,
    }
    values.update(overrides)
    return RuntimeSettings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    (
        "claude_timeout_seconds",
        "max_processing_seconds",
        "tool_timeout_seconds",
        "tool_max_timeout_seconds",
    ),
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_runtime_float_budgets_must_be_finite(tmp_path: Path, field: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        _settings(tmp_path, **{field: value})


def test_processing_budget_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_processing_seconds"):
        _settings(tmp_path, max_processing_seconds=0)

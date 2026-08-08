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


def test_equal_context_budgets_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_skill_context_chars must be below max_context_chars"):
        _settings(tmp_path, max_context_chars=24_000, max_skill_context_chars=24_000)


def test_skill_budget_above_context_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_skill_context_chars must be below max_context_chars"):
        _settings(tmp_path, max_context_chars=24_000, max_skill_context_chars=30_000)


def test_from_env_equal_budgets_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIDAY_WORKER_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FRIDAY_RUNTIME_MAX_CONTEXT_CHARS", "1_000")
    monkeypatch.setenv("FRIDAY_RUNTIME_MAX_SKILL_CONTEXT_CHARS", "1_000")
    with pytest.raises(ValueError, match="max_skill_context_chars must be below max_context_chars"):
        RuntimeSettings.from_env()


def test_from_env_greater_skill_budget_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRIDAY_WORKER_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FRIDAY_RUNTIME_MAX_CONTEXT_CHARS", "1_000")
    monkeypatch.setenv("FRIDAY_RUNTIME_MAX_SKILL_CONTEXT_CHARS", "2_000")
    with pytest.raises(ValueError, match="max_skill_context_chars must be below max_context_chars"):
        RuntimeSettings.from_env()


def test_skill_budget_one_below_context_preserved(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_context_chars=10_000, max_skill_context_chars=9_999)
    assert settings.max_skill_context_chars == 9_999
    assert settings.max_context_chars == 10_000


def test_defaults_remain_valid(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert settings.max_skill_context_chars == 24_000
    assert settings.max_context_chars == 60_000


def test_no_mutation_after_construction(tmp_path: Path) -> None:
    settings = _settings(tmp_path, max_context_chars=10_000, max_skill_context_chars=5_000)
    assert settings.max_skill_context_chars == 5_000
    assert settings.max_context_chars == 10_000


def test_from_env_invalid_numeric_value_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FRIDAY_WORKER_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FRIDAY_RUNTIME_MAX_CONTEXT_CHARS", "not-a-number")
    with pytest.raises(ValueError):
        RuntimeSettings.from_env()

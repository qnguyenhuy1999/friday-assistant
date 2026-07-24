"""Worker preflight: validate the environment without claiming or executing
any Run. Invoked via `just worker-check` (python -m apps.worker.preflight).

Checks: database connectivity, Alembic migration head, Claude executable +
version + brain-only flag support, and workspace accessibility."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from apps.worker.runtime_settings import RuntimeSettings
from apps.worker.settings import WorkerSettings
from friday.application.errors import BrainUnavailable, WorkspaceAccessDenied
from friday.infrastructure.brain.claude_cli import (
    ClaudeCliSettings,
    verify_brain_only_support,
)
from friday.infrastructure.persistence.database import create_engine
from friday.infrastructure.tools.workspace_paths import resolve_workspace_root


@dataclass(frozen=True, slots=True)
class PreflightReport:
    checks: tuple[tuple[str, bool, str], ...]

    @property
    def ok(self) -> bool:
        return all(passed for _, passed, _ in self.checks)


def run_preflight(
    settings: WorkerSettings,
    runtime: RuntimeSettings,
    *,
    alembic_ini: Path = Path("alembic.ini"),
) -> PreflightReport:
    checks: list[tuple[str, bool, str]] = []
    checks.append(_check_database(settings, alembic_ini))
    checks.append(_check_migration_head(settings, alembic_ini))
    checks.append(_check_claude(runtime))
    checks.append(_check_workspace(runtime))
    return PreflightReport(checks=tuple(checks))


def _check_database(settings: WorkerSettings, alembic_ini: Path) -> tuple[str, bool, str]:
    try:
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - preflight reports, never crashes
        return ("database", False, type(exc).__name__)
    return ("database", True, "reachable")


def _check_migration_head(settings: WorkerSettings, alembic_ini: Path) -> tuple[str, bool, str]:
    try:
        script = ScriptDirectory.from_config(Config(str(alembic_ini)))
        expected = script.get_current_head()
        engine = create_engine(settings.database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
        finally:
            engine.dispose()
        if row is None:
            return ("migration_head", False, "alembic_version is empty")
        if row[0] != expected:
            return ("migration_head", False, f"database at {row[0]}, head is {expected}")
    except Exception as exc:  # noqa: BLE001 - preflight reports, never crashes
        return ("migration_head", False, type(exc).__name__)
    return ("migration_head", True, str(expected))


def _check_claude(runtime: RuntimeSettings) -> tuple[str, bool, str]:
    try:
        version = verify_brain_only_support(
            ClaudeCliSettings(
                executable=runtime.claude_executable,
                model=runtime.claude_model,
                timeout_seconds=runtime.claude_timeout_seconds,
                max_output_bytes=runtime.claude_max_output_bytes,
            )
        )
    except BrainUnavailable as exc:
        return ("claude_brain_only", False, str(exc))
    return ("claude_brain_only", True, version)


def _check_workspace(runtime: RuntimeSettings) -> tuple[str, bool, str]:
    try:
        root = resolve_workspace_root(runtime.workspace_root)
    except WorkspaceAccessDenied as exc:
        return ("workspace", False, str(exc))
    return ("workspace", True, str(root))


def main() -> int:
    settings = WorkerSettings.from_env()
    runtime = RuntimeSettings.from_env()
    report = run_preflight(settings, runtime)
    for name, passed, detail in report.checks:
        status = "ok" if passed else "FAIL"
        print(f"{status:4} {name}: {detail}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())

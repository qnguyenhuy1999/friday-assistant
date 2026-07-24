from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def test_upgrade_creates_all_lifecycle_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    command.upgrade(_alembic_config(db_path), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        assert set(inspect(engine).get_table_names()) == {
            "tasks",
            "task_events",
            "runs",
            "run_steps",
            "approval_requests",
            "artifacts",
            "tool_invocations",
            "run_events",
            "run_work_items",
            "run_event_sequence_counters",
            "task_event_sequence_counters",
            "alembic_version",
        }
    finally:
        engine.dispose()


def test_downgrade_then_upgrade_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        # Alembic's online downgrade clears the alembic_version table's rows but
        # doesn't drop the table itself (upstream issue sqlalchemy/alembic#545);
        # only the --sql/offline path drops it. All application tables must be gone.
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        assert "tasks" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

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
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "tasks",
            "task_events",
            "runs",
            "run_steps",
            "approval_requests",
            "artifacts",
            "tool_invocations",
            "outbound_deliveries",
            "run_events",
            "run_work_items",
            "run_event_sequence_counters",
            "task_event_sequence_counters",
            "memory_index_snapshots",
            "memory_retrieval_records",
            "memory_retrieval_items",
            "schedules",
            "schedule_fires",
            "conversations",
            "conversation_turns",
            "alembic_version",
        }
        assert "execution_id" in {column["name"] for column in inspector.get_columns("runs")}
        checks = {check["name"] for check in inspector.get_check_constraints("schedules")}
        assert {
            "ck_schedules_kind",
            "ck_schedules_kind_shape",
            "ck_schedules_status_next_fire",
        } <= checks
        turn_checks = {
            check["name"] for check in inspector.get_check_constraints("conversation_turns")
        }
        assert "ck_conversation_turns_input_mode" in turn_checks
        delivery_checks = {
            check["name"] for check in inspector.get_check_constraints("outbound_deliveries")
        }
        assert {
            "ck_outbound_deliveries_status",
            "ck_outbound_deliveries_attempt_count",
            "ck_outbound_deliveries_source_shape",
            "ck_outbound_deliveries_route_fingerprint_hex",
            "ck_outbound_deliveries_body_sha256_hex",
        } <= delivery_checks
        delivery_unique = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("outbound_deliveries")
        }
        assert delivery_unique == {
            ("source_tool_invocation_id",),
            ("source_schedule_fire_id",),
        }
        delivery_columns = {
            column["name"]: column for column in inspector.get_columns("outbound_deliveries")
        }
        # 0013: the durable dispatch boundary marker. Nullable by design —
        # NULL means the external side effect definitely has not begun.
        assert "dispatch_started_at" in delivery_columns
        assert delivery_columns["dispatch_started_at"]["nullable"] is True
    finally:
        engine.dispose()


def test_delivery_claim_fencing_revision_is_reversible(tmp_path: Path) -> None:
    """0013 must add and drop dispatch_started_at without losing the table."""
    db_path = tmp_path / "migrate-0013.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0013")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("outbound_deliveries")}
        assert "dispatch_started_at" in columns
    finally:
        engine.dispose()

    command.downgrade(config, "0012")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        assert "outbound_deliveries" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("outbound_deliveries")}
        assert "dispatch_started_at" not in columns
        assert "body_sha256" in columns
    finally:
        engine.dispose()

    command.upgrade(config, "0013")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("outbound_deliveries")}
        assert "dispatch_started_at" in columns
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

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from friday.application.worker_maintenance import MaterializeScheduledAnswerDeliveries
from friday.domain.identifiers import ScheduleFireId
from friday.infrastructure.persistence.database import create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

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
            "delivery_attempts",
            "run_events",
            "run_work_items",
            "run_event_sequence_counters",
            "task_event_sequence_counters",
            "memory_index_snapshots",
            "memory_retrieval_records",
            "memory_retrieval_items",
            "schedules",
            "schedule_fires",
            "schedule_delivery_policies",
            "schedule_fire_delivery_plans",
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


def test_0016_preserves_historical_delivery_plans_without_inferred_authority(
    tmp_path: Path,
) -> None:
    """A real 0015 database upgrades without fabricating route body authority."""
    db_path = tmp_path / "migrate-0016-history.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0015")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        at = "2026-01-02 03:00:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks VALUES "
                    "('00000000-0000-0000-0000-000000000001', 't', '', 'pending', :at, "
                    "NULL, NULL, NULL, NULL, NULL)"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO runs VALUES "
                    "('00000000-0000-0000-0000-000000000002', "
                    "'00000000-0000-0000-0000-000000000001', 'succeeded', :at, :at, :at, "
                    "NULL, NULL, '00000000-0000-0000-0000-000000000002')"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO runs VALUES "
                    "('00000000-0000-0000-0000-000000000003', "
                    "'00000000-0000-0000-0000-000000000001', 'succeeded', :at, :at, :at, "
                    "NULL, NULL, '00000000-0000-0000-0000-000000000003')"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO schedules VALUES "
                    "('00000000-0000-0000-0000-000000000004', "
                    "'00000000-0000-0000-0000-000000000001', 'once', NULL, :at, 'UTC', "
                    "'completed', NULL, "
                    ":at, :at)"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO schedules VALUES "
                    "('00000000-0000-0000-0000-000000000005', "
                    "'00000000-0000-0000-0000-000000000001', 'once', NULL, :at, 'UTC', "
                    "'completed', "
                    "NULL, :at, :at)"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_fires VALUES "
                    "('00000000-0000-0000-0000-000000000006', "
                    "'00000000-0000-0000-0000-000000000004', :at, :at, "
                    "'00000000-0000-0000-0000-000000000002')"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_fires VALUES "
                    "('00000000-0000-0000-0000-000000000007', "
                    "'00000000-0000-0000-0000-000000000005', :at, :at, "
                    "'00000000-0000-0000-0000-000000000003')"
                ),
                {"at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_fire_delivery_plans VALUES "
                    "('00000000-0000-0000-0000-000000000008', "
                    "'00000000-0000-0000-0000-000000000006', "
                    "'00000000-0000-0000-0000-000000000004', "
                    "'00000000-0000-0000-0000-000000000002', 'ops.primary', "
                    ":fingerprint, 'final_agent_summary_v1', 'ready', NULL, :at)"
                ),
                {"fingerprint": "a" * 64, "at": at},
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_fire_delivery_plans VALUES "
                    "('00000000-0000-0000-0000-000000000009', "
                    "'00000000-0000-0000-0000-000000000007', "
                    "'00000000-0000-0000-0000-000000000005', "
                    "'00000000-0000-0000-0000-000000000003', 'ops.primary', NULL, "
                    "'final_agent_summary_v1', "
                    "'suppressed', 'schedule_delivery_route_disabled', :at)"
                ),
                {"at": at},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "0016")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, route_max_body_chars, content_rejected_run_id, status "
                    "FROM schedule_fire_delivery_plans ORDER BY id"
                )
            ).all()
        assert list(rows) == [
            ("00000000-0000-0000-0000-000000000008", None, None, "ready"),
            ("00000000-0000-0000-0000-000000000009", None, None, "suppressed"),
        ]
        checks = {
            check["sqltext"]
            for check in inspect(engine).get_check_constraints("schedule_fire_delivery_plans")
        }
        assert any("route_max_body_chars IS NULL" in check for check in checks)
    finally:
        engine.dispose()

    class Clock:
        def now(self) -> datetime:
            return datetime(2026, 1, 2, 3, tzinfo=UTC)

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        assert MaterializeScheduledAnswerDeliveries(factory, Clock(), batch_size=10).execute() == 0
        with factory() as uow:
            ready = uow.schedule_fire_delivery_plans.get_by_fire(
                ScheduleFireId.parse("00000000-0000-0000-0000-000000000006")
            )
            suppressed = uow.schedule_fire_delivery_plans.get_by_fire(
                ScheduleFireId.parse("00000000-0000-0000-0000-000000000007")
            )
        assert ready is not None and ready.route_max_body_chars is None
        assert ready.content_rejected_run_id is not None
        assert suppressed is not None and suppressed.status.value == "suppressed"
    finally:
        engine.dispose()

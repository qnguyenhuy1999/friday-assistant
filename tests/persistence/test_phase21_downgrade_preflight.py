"""SQLite proofs that unmerged Phase-21 downgrades are lossless or refuse."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"

TASK_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000002"
AGENT_ID = "00000000-0000-0000-0000-000000000003"
REVISION_ID = "00000000-0000-0000-0000-000000000004"
BINDING_TASK_ID = "00000000-0000-0000-0000-000000000005"
RESOLUTION_ID = "00000000-0000-0000-0000-000000000006"
DELEGATION_ID = "00000000-0000-0000-0000-000000000007"


def _config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _revision(db_path: Path) -> str | None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def _schema_fingerprint(db_path: Path) -> tuple[tuple[str, str, str | None], ...]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT name, type, sql FROM sqlite_master "
                    "WHERE type IN ('table', 'index', 'trigger', 'view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name, sql"
                )
            ).all()
        return tuple((row[0], row[1], row[2]) for row in rows)
    finally:
        engine.dispose()


def _seed_delegation(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks (id, title, description, status, created_at) "
                    "VALUES (:id, 'parent', '', 'pending', :at)"
                ),
                {"id": TASK_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO runs (id, task_id, execution_id, status, created_at) "
                    "VALUES (:id, :task_id, :execution_id, 'queued', :at)"
                ),
                {"id": RUN_ID, "task_id": TASK_ID, "execution_id": RUN_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO agents (id, key, display_name, description, status, "
                    "active_revision_id, created_at, updated_at) VALUES "
                    "(:id, 'downgrade.target', 'Target', '', 'active', NULL, :at, :at)"
                ),
                {"id": AGENT_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO delegation_requests ("
                    "id, parent_run_id, parent_run_step_id, target_agent_id, objective, "
                    "input_payload, expected_output_contract, authorization_fingerprint, status, "
                    "child_task_id, child_run_id, created_at, started_at, completed_at, "
                    "failure_code)"
                    " VALUES (:id, :run_id, NULL, :agent_id, 'delegate', '{}', 'result', "
                    ":fingerprint, "
                    "'requested', NULL, NULL, :at, NULL, NULL, NULL)"
                ),
                {
                    "id": DELEGATION_ID,
                    "run_id": RUN_ID,
                    "agent_id": AGENT_ID,
                    "fingerprint": "a" * 64,
                    "at": AT,
                },
            )
    finally:
        engine.dispose()


def _seed_agent_state(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks (id, title, description, status, created_at) "
                    "VALUES (:id, 'agent task', '', 'pending', :at)"
                ),
                {"id": TASK_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO runs (id, task_id, execution_id, status, created_at) "
                    "VALUES (:id, :task_id, :execution_id, 'queued', :at)"
                ),
                {"id": RUN_ID, "task_id": TASK_ID, "execution_id": RUN_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO agents (id, key, display_name, description, status, "
                    "active_revision_id, created_at, updated_at) VALUES "
                    "(:id, 'downgrade.agent', 'Agent', '', 'active', NULL, :at, :at)"
                ),
                {"id": AGENT_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO agent_revisions ("
                    "id, agent_id, version, instructions, runtime_kind, runtime_config, "
                    "content_sha256, source_kind, created_at"
                    ") VALUES (:id, :agent_id, 1, 'instructions', 'claude_cli', '{}', :sha, "
                    "'operator', :at)"
                ),
                {"id": REVISION_ID, "agent_id": AGENT_ID, "sha": "b" * 64, "at": AT},
            )
            connection.execute(
                text("UPDATE agents SET active_revision_id = :revision_id WHERE id = :id"),
                {"id": AGENT_ID, "revision_id": REVISION_ID},
            )
            connection.execute(
                text(
                    "INSERT INTO tasks (id, title, description, status, created_at) "
                    "VALUES (:id, 'binding task', '', 'pending', :at)"
                ),
                {"id": BINDING_TASK_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO task_agent_bindings (task_id, agent_id, created_at) "
                    "VALUES (:task_id, :agent_id, :at)"
                ),
                {"task_id": BINDING_TASK_ID, "agent_id": AGENT_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO run_agent_resolutions ("
                    "id, run_id, agent_id, revision_id, resolved_at"
                    ") VALUES (:id, :run_id, :agent_id, :revision_id, :at)"
                ),
                {
                    "id": RESOLUTION_ID,
                    "run_id": RUN_ID,
                    "agent_id": AGENT_ID,
                    "revision_id": REVISION_ID,
                    "at": AT,
                },
            )
    finally:
        engine.dispose()


def test_empty_phase21_downgrade_upgrade_cycle_is_compatible(tmp_path: Path) -> None:
    db_path = tmp_path / "phase21-empty.db"
    config = _config(db_path)
    command.upgrade(config, "head")

    command.downgrade(config, "0031")
    assert _revision(db_path) == "0031"
    command.upgrade(config, "head")
    assert _revision(db_path) == "0032"

    command.downgrade(config, "0030")
    assert _revision(db_path) == "0030"
    command.upgrade(config, "head")
    assert _revision(db_path) == "0032"


def test_0032_populated_downgrade_refuses_before_schema_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "phase21-delegation.db"
    config = _config(db_path)
    command.upgrade(config, "head")
    _seed_delegation(db_path)
    before_revision = _revision(db_path)
    before_schema = _schema_fingerprint(db_path)

    with pytest.raises(RuntimeError, match="0032 cannot downgrade"):
        command.downgrade(config, "0031")

    assert _revision(db_path) == before_revision == "0032"
    assert _schema_fingerprint(db_path) == before_schema


def test_0031_populated_downgrade_refuses_before_schema_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "phase21-agent.db"
    config = _config(db_path)
    command.upgrade(config, "0031")
    _seed_agent_state(db_path)
    before_revision = _revision(db_path)
    before_schema = _schema_fingerprint(db_path)

    with pytest.raises(RuntimeError, match="0031 cannot downgrade"):
        command.downgrade(config, "0030")

    assert _revision(db_path) == before_revision == "0031"
    assert _schema_fingerprint(db_path) == before_schema

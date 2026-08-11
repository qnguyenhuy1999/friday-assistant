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
CHILD_TASK_ID = "00000000-0000-0000-0000-000000000008"
CHILD_RUN_ID = "00000000-0000-0000-0000-000000000009"
WORKFLOW_ID = "00000000-0000-0000-0000-000000000010"
WORKFLOW_REVISION_ID = "00000000-0000-0000-0000-000000000011"
WORKFLOW_NODE_A_ID = "00000000-0000-0000-0000-000000000012"
WORKFLOW_NODE_B_ID = "00000000-0000-0000-0000-000000000013"
WORKFLOW_EDGE_ID = "00000000-0000-0000-0000-000000000014"


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


def _seed_step2_state(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks (id, title, description, status, created_at) "
                    "VALUES (:id, 'child', '', 'active', :at)"
                ),
                {"id": CHILD_TASK_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO runs (id, task_id, execution_id, status, created_at) "
                    "VALUES (:id, :task_id, :execution_id, 'queued', :at)"
                ),
                {
                    "id": CHILD_RUN_ID,
                    "task_id": CHILD_TASK_ID,
                    "execution_id": CHILD_RUN_ID,
                    "at": AT,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO task_agent_bindings (task_id, agent_id, created_at) "
                    "VALUES (:task_id, :agent_id, :at)"
                ),
                {"task_id": CHILD_TASK_ID, "agent_id": AGENT_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO delegation_requests ("
                    "id, parent_run_id, parent_run_step_id, target_agent_id, objective, "
                    "input_payload, expected_output_contract, authorization_fingerprint, status, "
                    "child_task_id, child_run_id, created_at, started_at, completed_at, "
                    "failure_code) VALUES (:id, :parent_id, NULL, :agent_id, 'delegate', '{}', "
                    "'result', :fingerprint, 'dispatched', :child_task_id, :child_run_id, "
                    ":at, :at, NULL, NULL)"
                ),
                {
                    "id": DELEGATION_ID,
                    "parent_id": RUN_ID,
                    "agent_id": AGENT_ID,
                    "fingerprint": "c" * 64,
                    "child_task_id": CHILD_TASK_ID,
                    "child_run_id": CHILD_RUN_ID,
                    "at": AT,
                },
            )
            connection.execute(
                text(
                    "UPDATE runs SET status = 'waiting_for_delegation', "
                    "delegation_request_id = :delegation_id WHERE id = :run_id"
                ),
                {"delegation_id": DELEGATION_ID, "run_id": RUN_ID},
            )
    finally:
        engine.dispose()


def _durable_state_snapshot(db_path: Path) -> tuple[object, ...]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).all()
            delegations = connection.execute(
                text("SELECT * FROM delegation_requests ORDER BY id")
            ).all()
            runs = connection.execute(text("SELECT * FROM runs ORDER BY id")).all()
            bindings = connection.execute(
                text("SELECT * FROM task_agent_bindings ORDER BY task_id")
            ).all()
        return (revision, _schema_fingerprint(db_path), delegations, runs, bindings)
    finally:
        engine.dispose()


def _workflow_state_snapshot(db_path: Path) -> tuple[object, ...]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            tables = (
                "workflows",
                "workflow_revisions",
                "workflow_nodes",
                "workflow_edges",
            )
            rows = tuple(
                (
                    table,
                    connection.execute(text(f"SELECT * FROM {table} ORDER BY 1")).all(),
                )
                for table in tables
            )
        return (_revision(db_path), _schema_fingerprint(db_path), rows)
    finally:
        engine.dispose()


def _seed_workflow_state(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO agents (id, key, display_name, description, status, "
                    "active_revision_id, created_at, updated_at) VALUES "
                    "(:id, '0034.workflow.target', 'Target', '', 'active', NULL, :at, :at)"
                ),
                {"id": AGENT_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO workflows "
                    "(id, key, display_name, description, status, active_revision_id, "
                    "created_at, updated_at) VALUES "
                    "(:id, '0034.workflow', 'Workflow', '', 'active', NULL, :at, :at)"
                ),
                {"id": WORKFLOW_ID, "at": AT},
            )
            connection.execute(
                text(
                    "INSERT INTO workflow_revisions "
                    "(id, workflow_id, version, content_sha256, source_kind, created_at) "
                    "VALUES (:id, :workflow_id, 1, :sha, 'operator', :at)"
                ),
                {
                    "id": WORKFLOW_REVISION_ID,
                    "workflow_id": WORKFLOW_ID,
                    "sha": "a" * 64,
                    "at": AT,
                },
            )
            for node_id, node_key in (
                (WORKFLOW_NODE_A_ID, "a"),
                (WORKFLOW_NODE_B_ID, "b"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO workflow_nodes "
                        "(id, revision_id, node_key, target_agent_id, objective, input_payload, "
                        "expected_output_contract, created_at) VALUES "
                        "(:id, :revision_id, :node_key, :agent_id, :objective, '{}', 'done', :at)"
                    ),
                    {
                        "id": node_id,
                        "revision_id": WORKFLOW_REVISION_ID,
                        "node_key": node_key,
                        "agent_id": AGENT_ID,
                        "objective": node_key,
                        "at": AT,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO workflow_edges "
                    "(id, revision_id, from_node_id, to_node_id, created_at) "
                    "VALUES (:id, :revision_id, :from_id, :to_id, :at)"
                ),
                {
                    "id": WORKFLOW_EDGE_ID,
                    "revision_id": WORKFLOW_REVISION_ID,
                    "from_id": WORKFLOW_NODE_A_ID,
                    "to_id": WORKFLOW_NODE_B_ID,
                    "at": AT,
                },
            )
            connection.execute(
                text("UPDATE workflows SET active_revision_id = :revision_id WHERE id = :id"),
                {"id": WORKFLOW_ID, "revision_id": WORKFLOW_REVISION_ID},
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
    assert _revision(db_path) == "0034"

    command.downgrade(config, "0030")
    assert _revision(db_path) == "0030"
    command.upgrade(config, "head")
    assert _revision(db_path) == "0034"


def test_0032_populated_downgrade_refuses_before_schema_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "phase21-delegation.db"
    config = _config(db_path)
    command.upgrade(config, "0032")
    _seed_delegation(db_path)
    before_revision = _revision(db_path)
    before_schema = _schema_fingerprint(db_path)

    with pytest.raises(RuntimeError, match="0032 cannot downgrade"):
        command.downgrade(config, "0031")

    assert _revision(db_path) == before_revision == "0032"
    assert _schema_fingerprint(db_path) == before_schema


def test_0032_requested_delegation_rows_upgrade_compatibly_to_0033(tmp_path: Path) -> None:
    db_path = tmp_path / "phase21-compatible-upgrade.db"
    config = _config(db_path)
    command.upgrade(config, "0032")
    _seed_delegation(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            before = connection.execute(text("SELECT * FROM delegation_requests ORDER BY id")).all()
    finally:
        engine.dispose()

    command.upgrade(config, "0033")
    assert _revision(db_path) == "0033"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            after = connection.execute(text("SELECT * FROM delegation_requests ORDER BY id")).all()
        assert after == before
    finally:
        engine.dispose()


def test_0033_empty_downgrade_to_0032_is_compatible(tmp_path: Path) -> None:
    db_path = tmp_path / "phase21-empty-0033.db"
    config = _config(db_path)
    command.upgrade(config, "0033")
    command.downgrade(config, "0032")
    assert _revision(db_path) == "0032"


def test_0033_populated_downgrade_refuses_before_any_state_or_ddl_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "phase21-populated-0033.db"
    config = _config(db_path)
    command.upgrade(config, "0033")
    # Seed the parent/child/request in a valid Step-2 shape, including both
    # ownership edges introduced by 0033.
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
                    "(:id, '0033.target', 'Target', '', 'active', NULL, :at, :at)"
                ),
                {"id": AGENT_ID, "at": AT},
            )
    finally:
        engine.dispose()
    _seed_step2_state(db_path)
    before = _durable_state_snapshot(db_path)

    with pytest.raises(RuntimeError, match="0033 cannot downgrade"):
        command.downgrade(config, "0032")

    assert _durable_state_snapshot(db_path) == before


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


def test_0033_to_0034_and_empty_0034_downgrade_upgrade_cycle_is_lossless(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "phase21-empty-0034.db"
    config = _config(db_path)

    command.upgrade(config, "0033")
    assert _revision(db_path) == "0033"
    command.upgrade(config, "0034")
    assert _revision(db_path) == "0034"
    empty_0034 = _workflow_state_snapshot(db_path)

    command.downgrade(config, "0033")
    assert _revision(db_path) == "0033"
    assert all(
        table not in {row[0] for row in _schema_fingerprint(db_path)}
        for table in ("workflows", "workflow_revisions", "workflow_nodes", "workflow_edges")
    )

    command.upgrade(config, "0034")
    assert _revision(db_path) == "0034"
    after = _workflow_state_snapshot(db_path)
    assert after[0] == empty_0034[0] == "0034"
    assert after[2] == empty_0034[2]


def test_0034_populated_downgrade_refuses_before_schema_or_row_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "phase21-populated-0034.db"
    config = _config(db_path)
    command.upgrade(config, "0034")
    _seed_workflow_state(db_path)
    before = _workflow_state_snapshot(db_path)

    with pytest.raises(RuntimeError, match="0034 cannot downgrade"):
        command.downgrade(config, "0033")

    assert _workflow_state_snapshot(db_path) == before

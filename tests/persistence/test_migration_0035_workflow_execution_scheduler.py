from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"
WID = "00000000-0000-0000-0000-000000000034"
RID = "00000000-0000-0000-0000-000000000035"
TID = "00000000-0000-0000-0000-000000000036"


def cfg(path: Any) -> Config:
    c = Config(str(ROOT / "alembic.ini"))
    c.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return c


def rev(path: Any) -> Any:
    e = create_engine(f"sqlite:///{path}")
    try:
        with e.connect() as conn:
            stmt = text("SELECT version_num FROM alembic_version")
            return conn.execute(stmt).scalar_one_or_none()
    finally:
        e.dispose()


def rows(path: Any, table: str) -> Any:
    e = create_engine(f"sqlite:///{path}")
    try:
        with e.connect() as conn:
            stmt = text(f"SELECT * FROM {table} ORDER BY 1")
            return tuple(conn.execute(stmt).all())
    finally:
        e.dispose()


def seed_step3(path: Any) -> None:
    e = create_engine(f"sqlite:///{path}")
    try:
        with e.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workflows "
                    "(id,key,display_name,description,status,"
                    "active_revision_id,created_at,updated_at) "
                    'VALUES (:id,:key,:name,:desc,"active",NULL,:at,:at)'
                ),
                {"id": WID, "key": "migration.workflow", "name": "Migration", "desc": "", "at": AT},
            )
            conn.execute(
                text(
                    "INSERT INTO workflow_revisions "
                    "(id,workflow_id,version,content_sha256,source_kind,created_at) "
                    'VALUES (:id,:wid,1,:sha,"operator",:at)'
                ),
                {"id": RID, "wid": WID, "sha": "a" * 64, "at": AT},
            )
            conn.execute(
                text("UPDATE workflows SET active_revision_id=:rid WHERE id=:wid"),
                {"rid": RID, "wid": WID},
            )
    finally:
        e.dispose()


def seed_binding(path: Any) -> None:
    e = create_engine(f"sqlite:///{path}")
    try:
        with e.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tasks "
                    "(id,title,description,status,created_at,started_at,"
                    "completed_at,failed_at,cancelled_at,failure) "
                    'VALUES (:id,"root","","created",:at,NULL,NULL,NULL,NULL,NULL)'
                ),
                {"id": TID, "at": AT},
            )
            conn.execute(
                text(
                    "INSERT INTO task_workflow_bindings "
                    "(task_id,workflow_id,created_at,updated_at) "
                    "VALUES (:tid,:wid,:at,:at)"
                ),
                {"tid": TID, "wid": WID, "at": AT},
            )
    finally:
        e.dispose()


def test_step3_state_survives_upgrade(tmp_path: Any) -> None:
    path = tmp_path / "step3.db"
    c = cfg(path)
    command.upgrade(c, "0034")
    seed_step3(path)
    before = rows(path, "workflows")
    command.upgrade(c, "0035")
    assert rev(path) == "0035"
    assert rows(path, "workflows") == before
    assert rows(path, "workflow_revisions")


def test_empty_round_trip(tmp_path: Any) -> None:
    path = tmp_path / "empty.db"
    c = cfg(path)
    command.upgrade(c, "0034")
    command.upgrade(c, "0035")
    command.downgrade(c, "0034")
    assert rev(path) == "0034"
    command.upgrade(c, "0035")
    assert rev(path) == "0035"


def test_populated_downgrade_refuses_before_ddl(tmp_path: Any) -> None:
    path = tmp_path / "refuse.db"
    c = cfg(path)
    command.upgrade(c, "0034")
    seed_step3(path)
    command.upgrade(c, "0035")
    seed_binding(path)
    before = rows(path, "task_workflow_bindings")
    with pytest.raises(RuntimeError, match="0035 cannot downgrade"):
        command.downgrade(c, "0034")
    assert rev(path) == "0035"
    assert rows(path, "task_workflow_bindings") == before


def test_rejected_downgrade_preserves_state(tmp_path: Any) -> None:
    path = tmp_path / "reject.db"
    c = cfg(path)
    command.upgrade(c, "0034")
    seed_step3(path)
    command.upgrade(c, "0035")
    seed_binding(path)
    before = (rows(path, "workflows"), rows(path, "task_workflow_bindings"))
    with pytest.raises(RuntimeError):
        command.downgrade(c, "0034")
    assert rev(path) == "0035"
    assert (rows(path, "workflows"), rows(path, "task_workflow_bindings")) == before

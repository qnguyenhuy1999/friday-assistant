"""Keep the Alembic schema and SQLAlchemy metadata structurally in sync."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.engine import Engine

from friday.infrastructure.persistence.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_VERSION_TABLE = "alembic_version"


def _alembic_config(db_path: Path) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _normalize_type(type_: Any) -> str:
    """Compare SQLite's portable type name, without incidental size syntax."""
    compiled = str(type_.compile(dialect=sqlite.dialect()))
    return re.sub(r"\([^)]*\)", "", compiled).strip().upper()


def _normalize_default(default: Any) -> str | None:
    if default is None:
        return None
    text = str(default).strip()
    return text or None


def _metadata_snapshot() -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for table in Base.metadata.sorted_tables:
        columns = {
            column.name: {
                "type": _normalize_type(column.type),
                "nullable": column.nullable,
                "primary_key": column.primary_key,
                "default": _normalize_default(
                    column.server_default.arg if column.server_default is not None else None
                ),
            }
            for column in table.columns
        }
        foreign_keys = sorted(
            (
                foreign_key.parent.name,
                foreign_key.column.table.name,
                foreign_key.column.name,
            )
            for column in table.columns
            for foreign_key in column.foreign_keys
        )
        unique_constraints = sorted(
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        )
        indexes = sorted(
            (index.name, tuple(column.name for column in index.columns), index.unique)
            for index in table.indexes
        )
        tables[table.name] = {
            "columns": columns,
            "primary_key": tuple(column.name for column in table.primary_key.columns),
            "foreign_keys": foreign_keys,
            "unique_constraints": unique_constraints,
            "indexes": indexes,
        }
    return tables


def _database_snapshot(engine: Engine) -> dict[str, dict[str, Any]]:
    inspector = inspect(engine)
    tables: dict[str, dict[str, Any]] = {}
    for table_name in inspector.get_table_names():
        if table_name == ALEMBIC_VERSION_TABLE:
            continue
        columns = {
            column["name"]: {
                "type": _normalize_type(column["type"]),
                "nullable": column["nullable"],
                "primary_key": column["name"]
                in inspector.get_pk_constraint(table_name)["constrained_columns"],
                "default": _normalize_default(column["default"]),
            }
            for column in inspector.get_columns(table_name)
        }
        primary_key = tuple(inspector.get_pk_constraint(table_name)["constrained_columns"])
        foreign_keys = sorted(
            (
                constrained,
                foreign_key["referred_table"],
                referred,
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
            for constrained, referred in zip(
                foreign_key["constrained_columns"],
                foreign_key["referred_columns"],
                strict=True,
            )
        )
        unique_constraints = sorted(
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table_name)
        )
        indexes = sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table_name)
        )
        tables[table_name] = {
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
            "unique_constraints": unique_constraints,
            "indexes": indexes,
        }
    return tables


def test_alembic_head_matches_sqlalchemy_metadata(tmp_path: Path) -> None:
    """A fresh Alembic upgrade must equal the current owned ORM schema."""
    db_path = tmp_path / "schema-parity.db"
    config = _alembic_config(db_path)
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None

    command.upgrade(config, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            *Base.metadata.tables,
            ALEMBIC_VERSION_TABLE,
        }
        with engine.connect() as connection:
            revision = connection.scalar(
                sa.select(sa.column("version_num")).select_from(
                    sa.table(ALEMBIC_VERSION_TABLE, sa.column("version_num"))
                )
            )
        assert revision == expected_head

        actual = _database_snapshot(engine)
        expected = _metadata_snapshot()
        assert set(actual) == set(expected)
        assert actual == expected
    finally:
        engine.dispose()


def test_schema_snapshot_comparison_detects_representative_drift() -> None:
    """The parity comparison must fail when a schema snapshot gains a column."""
    expected = _metadata_snapshot()
    drifted = deepcopy(expected)
    table_name = next(iter(drifted))
    drifted[table_name]["columns"]["schema_drift_probe"] = {
        "type": "INTEGER",
        "nullable": True,
        "primary_key": False,
        "default": None,
    }

    with pytest.raises(AssertionError):
        assert drifted == expected

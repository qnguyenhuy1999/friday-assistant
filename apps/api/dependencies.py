"""FastAPI dependency providers. Routes depend on these, never on
SQLAlchemy or the `UnitOfWork` implementation directly — swapping the
database location (e.g. for tests) only ever touches `app.state`.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from sqlalchemy import text

from apps.api.settings import ApiSettings
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.infrastructure.persistence.health import is_database_reachable


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    factory: UnitOfWorkFactory = request.app.state.uow_factory
    return factory


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_settings(request: Request) -> ApiSettings:
    settings: ApiSettings = request.app.state.settings
    return settings


def get_database_reachable(request: Request) -> bool:
    return is_database_reachable(request.app.state.engine)


def get_database_schema_current(request: Request) -> bool:
    """Check that this database has the Alembic head without mutating it."""
    try:
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        expected = ScriptDirectory.from_config(config).get_current_head()
        with request.app.state.engine.connect() as connection:
            version = cast(
                str | None,
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar(),
            )
        # Do not run migrations here: they remain an explicit operator action.
        return version == expected
    except Exception:  # noqa: BLE001 - readiness intentionally reports unavailable
        return False

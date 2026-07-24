"""Database reachability check. Isolated here so delivery-layer callers
(e.g. the API health route) never need to import SQLAlchemy or catch its
exceptions directly."""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError


def is_database_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True

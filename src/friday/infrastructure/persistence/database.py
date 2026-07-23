from __future__ import annotations

from sqlalchemy import Engine, event
from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy.orm import Session, sessionmaker

_BUSY_TIMEOUT_MS = 5000


def create_engine(database_url: str) -> Engine:
    """Create a SQLite engine with WAL, foreign keys, and a busy timeout.

    WAL mode has no effect on an in-memory database (SQLite ignores it) —
    tests against `sqlite://` verify `foreign_keys` only.
    """
    engine = _sa_create_engine(database_url)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)

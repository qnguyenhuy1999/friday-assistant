from __future__ import annotations

from sqlalchemy import text

from friday.infrastructure.persistence.database import create_engine, create_session_factory


def test_file_engine_enables_wal_foreign_keys_and_busy_timeout(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    engine.dispose()


def test_in_memory_engine_enables_foreign_keys_but_not_wal() -> None:
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        # SQLite silently ignores WAL for :memory: databases — documented limitation.
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "memory"
    engine.dispose()


def test_session_factory_produces_working_sessions(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()

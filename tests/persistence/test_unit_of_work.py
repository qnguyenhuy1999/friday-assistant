from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from friday.application.errors import TransactionFailure
from friday.domain import Task, TaskId
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.models import Base
from friday.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWork,
    create_unit_of_work_factory,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _task(title: str = "t") -> Task:
    return Task.new(id=TaskId.new(), title=title, description="d", created_at=T0)


def test_commit_persists_writes_across_all_repositories(
    session_factory: sessionmaker[Session],
) -> None:
    task = _task()
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        uow.tasks.add(task)
        uow.commit()

    with SqlAlchemyUnitOfWork(session_factory()) as new_uow:
        assert new_uow.tasks.get(task.id) is not None


def test_rollback_discards_staged_writes(session_factory: sessionmaker[Session]) -> None:
    task = _task()
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        uow.tasks.add(task)
        uow.rollback()

    with SqlAlchemyUnitOfWork(session_factory()) as new_uow:
        assert new_uow.tasks.get(task.id) is None


def test_exception_inside_context_manager_rolls_back(
    session_factory: sessionmaker[Session],
) -> None:
    task = _task()
    with pytest.raises(RuntimeError), SqlAlchemyUnitOfWork(session_factory()) as uow:
        uow.tasks.add(task)
        raise RuntimeError("boom")

    with SqlAlchemyUnitOfWork(session_factory()) as new_uow:
        assert new_uow.tasks.get(task.id) is None


def test_repositories_share_one_session(session_factory: sessionmaker[Session]) -> None:
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        assert uow.tasks._session is uow.runs._session
        assert uow.tasks._session is uow.events._session


def test_commit_failure_raises_transaction_failure_and_rolls_back(
    session_factory: sessionmaker[Session],
) -> None:
    task = _task()
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        uow.tasks.add(task)

        def _broken_commit() -> None:
            raise OperationalError("commit", {}, Exception("disk full"))

        uow._session.commit = _broken_commit  # type: ignore[method-assign]
        with pytest.raises(TransactionFailure):
            uow.commit()

    with SqlAlchemyUnitOfWork(session_factory()) as new_uow:
        assert new_uow.tasks.get(task.id) is None


def test_commit_failure_message_excludes_raw_db_detail(
    session_factory: sessionmaker[Session],
) -> None:
    task = _task()
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        uow.tasks.add(task)

        def _broken_commit() -> None:
            raise OperationalError(
                "INSERT INTO tasks (id) VALUES (?)", {"id": "secret-value"}, Exception("disk full")
            )

        uow._session.commit = _broken_commit  # type: ignore[method-assign]
        with pytest.raises(TransactionFailure) as exc_info:
            uow.commit()

    message = str(exc_info.value)
    assert "disk full" not in message
    assert "INSERT INTO" not in message
    assert "secret-value" not in message


def test_exit_closes_the_session(session_factory: sessionmaker[Session]) -> None:
    with SqlAlchemyUnitOfWork(session_factory()) as uow:
        session = uow.tasks._session
    assert not session.is_active or session.in_transaction() is False


def test_factory_produces_independent_units_of_work(
    session_factory: sessionmaker[Session],
) -> None:
    factory = create_unit_of_work_factory(session_factory)
    task = _task()
    with factory() as uow:
        uow.tasks.add(task)
        uow.commit()

    with factory() as new_uow:
        assert new_uow.tasks.get(task.id) is not None

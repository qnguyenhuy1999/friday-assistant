"""SQLAlchemy-backed `UnitOfWork` (see `friday.application.ports.UnitOfWork`).

One `Session` is shared by every repository exposed here; this class is the
sole place that calls `session.commit()`/`session.rollback()`/`session.close()`,
so no repository can commit independently of the transaction boundary. The
application-facing protocol never exposes the session itself.

Persistence failures are translated into the stable application error
hierarchy at this boundary — `IntegrityError`/`OperationalError`/
`StaleDataError` must never escape into application or use-case code.
"""

from __future__ import annotations

import contextlib
from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from friday.application.errors import ConcurrencyConflict, EntityConflict, TransactionFailure
from friday.application.ports import UnitOfWork, UnitOfWorkFactory
from friday.infrastructure.persistence.repositories import (
    ApprovalRepository,
    ArtifactRepository,
    RunEventStore,
    RunRepository,
    RunStepRepository,
    TaskRepository,
    ToolInvocationRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks = TaskRepository(session)
        self._runs = RunRepository(session)
        self._steps = RunStepRepository(session)
        self._approvals = ApprovalRepository(session)
        self._artifacts = ArtifactRepository(session)
        self._tool_invocations = ToolInvocationRepository(session)
        self._events = RunEventStore(session)

    @property
    def tasks(self) -> TaskRepository:
        return self._tasks

    @property
    def runs(self) -> RunRepository:
        return self._runs

    @property
    def steps(self) -> RunStepRepository:
        return self._steps

    @property
    def approvals(self) -> ApprovalRepository:
        return self._approvals

    @property
    def artifacts(self) -> ArtifactRepository:
        return self._artifacts

    @property
    def tool_invocations(self) -> ToolInvocationRepository:
        return self._tool_invocations

    @property
    def events(self) -> RunEventStore:
        return self._events

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._rollback_quietly()
        self._session.close()

    def commit(self) -> None:
        try:
            self._session.commit()
        except StaleDataError as exc:
            self._rollback_quietly()
            raise ConcurrencyConflict(str(exc)) from exc
        except IntegrityError as exc:
            self._rollback_quietly()
            raise EntityConflict(str(exc)) from exc
        except (OperationalError, SQLAlchemyError) as exc:
            self._rollback_quietly()
            raise TransactionFailure(str(exc)) from exc

    def rollback(self) -> None:
        try:
            self._session.rollback()
        except SQLAlchemyError as exc:
            raise TransactionFailure(str(exc)) from exc

    def _rollback_quietly(self) -> None:
        with contextlib.suppress(SQLAlchemyError):
            self._session.rollback()


def create_unit_of_work_factory(session_factory: sessionmaker[Session]) -> UnitOfWorkFactory:
    def factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory())

    return factory

"""FastAPI dependency providers. Routes depend on these, never on
SQLAlchemy or the `UnitOfWork` implementation directly — swapping the
database location (e.g. for tests) only ever touches `app.state`.
"""

from __future__ import annotations

from fastapi import Request

from friday.application.ports import Clock, UnitOfWorkFactory
from friday.infrastructure.persistence.health import is_database_reachable


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    factory: UnitOfWorkFactory = request.app.state.uow_factory
    return factory


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_database_reachable(request: Request) -> bool:
    return is_database_reachable(request.app.state.engine)

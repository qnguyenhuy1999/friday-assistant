"""API composition root: settings -> engine/session factory -> UnitOfWork
factory -> FastAPI app. `create_app` is the sole place infrastructure is
constructed; routes and dependencies only ever consume what is wired here.

Startup never mutates the database schema (no `metadata.create_all()`) --
schema evolution stays exclusively Alembic-owned.
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.api.errors import register_exception_handlers
from apps.api.routes.health import router as health_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.steps import router as steps_router
from apps.api.routes.tasks import router as tasks_router
from apps.api.settings import ApiSettings
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory


def create_app(settings: ApiSettings) -> FastAPI:
    app = FastAPI(title="Friday Agent OS API", version="0.1.0")

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.uow_factory = create_unit_of_work_factory(session_factory)
    app.state.clock = SystemClock()

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(tasks_router)
    app.include_router(runs_router)
    app.include_router(steps_router)

    return app

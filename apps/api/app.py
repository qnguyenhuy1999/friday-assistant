"""API composition root: settings -> engine/session factory -> UnitOfWork
factory -> FastAPI app. `create_app` is the sole place infrastructure is
constructed; routes and dependencies only ever consume what is wired here.

Startup never mutates the database schema (no `metadata.create_all()`) --
schema evolution stays exclusively Alembic-owned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.errors import ERROR_RESPONSES, register_exception_handlers
from apps.api.routes.approvals import router as approvals_router
from apps.api.routes.artifacts import router as artifacts_router
from apps.api.routes.events import router as events_router
from apps.api.routes.health import router as health_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.steps import router as steps_router
from apps.api.routes.tasks import router as tasks_router
from apps.api.routes.tool_invocations import router as tool_invocations_router
from apps.api.settings import ApiSettings
from friday.infrastructure.clock import SystemClock
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory


def create_app(settings: ApiSettings) -> FastAPI:
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="Friday Agent OS API", version="0.1.0", lifespan=lifespan)
    session_factory = create_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.uow_factory = create_unit_of_work_factory(session_factory)
    app.state.clock = SystemClock()

    register_exception_handlers(app)
    app.include_router(health_router, responses=ERROR_RESPONSES)
    app.include_router(tasks_router, responses=ERROR_RESPONSES)
    app.include_router(runs_router, responses=ERROR_RESPONSES)
    app.include_router(steps_router, responses=ERROR_RESPONSES)
    app.include_router(approvals_router, responses=ERROR_RESPONSES)
    app.include_router(tool_invocations_router, responses=ERROR_RESPONSES)
    app.include_router(artifacts_router, responses=ERROR_RESPONSES)
    app.include_router(events_router, responses=ERROR_RESPONSES)

    return app

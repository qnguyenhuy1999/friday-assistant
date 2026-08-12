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
from fastapi.middleware.cors import CORSMiddleware

from apps.api.errors import ERROR_RESPONSES, register_exception_handlers
from apps.api.request_size import RequestBodyLimitMiddleware
from apps.api.routes.agents import router as agents_router
from apps.api.routes.approvals import router as approvals_router
from apps.api.routes.artifacts import router as artifacts_router
from apps.api.routes.conversations import router as conversations_router
from apps.api.routes.delegations import router as delegations_router
from apps.api.routes.events import router as events_router
from apps.api.routes.health import router as health_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.schedules import router as schedules_router
from apps.api.routes.skills import router as skills_router
from apps.api.routes.steps import router as steps_router
from apps.api.routes.tasks import router as tasks_router
from apps.api.routes.tool_invocations import router as tool_invocations_router
from apps.api.routes.workflows import router as workflows_router
from apps.api.settings import ApiSettings
from friday.application.brain_runtime import BrainRuntime
from friday.application.brain_runtime_registry import DEFAULT_RUNTIME_KIND, BrainRuntimeRegistry
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
    brain_runtime_registry = BrainRuntimeRegistry()

    def _no_api_brain_calls() -> BrainRuntime:
        # The API process only validates `runtime_kind` at revision-creation
        # time (CreateAgentRevision calls `is_registered`, never `create`);
        # the worker's composition root is the sole place a brain runtime is
        # actually constructed and invoked.
        raise NotImplementedError("the API process never constructs a brain runtime")

    brain_runtime_registry.register(DEFAULT_RUNTIME_KIND, _no_api_brain_calls)
    app.state.brain_runtime_registry = brain_runtime_registry
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    register_exception_handlers(app)
    app.include_router(health_router, responses=ERROR_RESPONSES)
    app.include_router(tasks_router, responses=ERROR_RESPONSES)
    app.include_router(agents_router, responses=ERROR_RESPONSES)
    app.include_router(delegations_router, responses=ERROR_RESPONSES)
    app.include_router(skills_router, responses=ERROR_RESPONSES)
    app.include_router(schedules_router, responses=ERROR_RESPONSES)
    app.include_router(conversations_router, responses=ERROR_RESPONSES)
    app.include_router(runs_router, responses=ERROR_RESPONSES)
    app.include_router(steps_router, responses=ERROR_RESPONSES)
    app.include_router(approvals_router, responses=ERROR_RESPONSES)
    app.include_router(tool_invocations_router, responses=ERROR_RESPONSES)
    app.include_router(artifacts_router, responses=ERROR_RESPONSES)
    app.include_router(events_router, responses=ERROR_RESPONSES)
    app.include_router(workflows_router, responses=ERROR_RESPONSES)

    return app

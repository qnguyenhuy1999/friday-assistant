from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from starlette.testclient import TestClient

from apps.api.app import create_app
from apps.api.settings import ApiSettings
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import RunEventId, RunId, TaskEventId, TaskId
from friday.domain.run import Run
from friday.domain.task import Task
from friday.domain.task_event import TaskEvent, TaskEventType

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SeededRun:
    task_id: TaskId
    run_id: RunId


def _settings_for(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        host="127.0.0.1",
        port=8000,
        sse_poll_interval_seconds=0.001,
    )


def _upgrade_schema(database_url: str) -> None:
    """Exercise the production Alembic path used by deployed databases."""
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture
def app(tmp_path: Path) -> Iterator[FastAPI]:
    settings = _settings_for(tmp_path)
    _upgrade_schema(settings.database_url)
    application = create_app(settings)
    try:
        yield application
    finally:
        application.state.engine.dispose()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def seed_active_run(app: FastAPI) -> SeededRun:
    task = Task.new(id=TaskId.new(), title="API task", description="", created_at=NOW)
    task.start(NOW)
    with app.state.uow_factory() as uow:
        uow.tasks.add(task)
        uow.commit()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=NOW)
    run.start(NOW)
    with app.state.uow_factory() as uow:
        uow.runs.add(run)
        uow.commit()
    return SeededRun(task.id, run.id)


def append_run_event(app: FastAPI, run_id: RunId, sequence: int) -> None:
    with app.state.uow_factory() as uow:
        uow.events.append(
            RunEvent(
                id=RunEventId.new(),
                run_id=run_id,
                type=RunEventType.RUN_STARTED,
                sequence=sequence,
                occurred_at=NOW,
                payload={"sequence": sequence},
            )
        )
        uow.commit()


def append_task_event(app: FastAPI, task_id: TaskId, sequence: int) -> None:
    with app.state.uow_factory() as uow:
        uow.task_events.append(
            TaskEvent(
                id=TaskEventId.new(),
                task_id=task_id,
                type=TaskEventType.TASK_COMPLETED,
                sequence=sequence,
                occurred_at=NOW,
                payload={"sequence": sequence},
            )
        )
        uow.commit()

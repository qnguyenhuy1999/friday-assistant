from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from friday.domain import (
    DeliveryId,
    DeliverySourceKind,
    DeliveryStatus,
    OutboundDelivery,
    Run,
    RunId,
    Task,
    TaskId,
    ToolInvocation,
    ToolInvocationId,
)
from friday.domain.identifiers import ScheduleFireId, ScheduleId
from friday.infrastructure.persistence.models import RunRow, ScheduleFireRow, ScheduleRow
from friday.infrastructure.persistence.repositories import (
    OutboundDeliveryRepository,
    RunRepository,
    TaskRepository,
    ToolInvocationRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINGERPRINT = "a" * 64


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    """Acceptance tests use the actual Alembic-created SQLite schema."""
    db_path = tmp_path / "outbound-deliveries.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    db_session = sessionmaker(bind=engine)()
    try:
        yield db_session
    finally:
        db_session.close()
        engine.dispose()


def _run_and_invocation(session: Session) -> tuple[RunId, ToolInvocationId]:
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    TaskRepository(session).add(task)
    session.flush()
    run = Run.new(id=RunId.new(), task_id=task.id, created_at=T0)
    RunRepository(session).add(run)
    session.flush()
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run.id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    ToolInvocationRepository(session).add(invocation)
    session.flush()
    return run.id, invocation.id


def _delivery(
    run_id: RunId, invocation_id: ToolInvocationId, available_at: datetime
) -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=run_id,
        source_tool_invocation_id=invocation_id,
        route_id="personal.notifications",
        route_fingerprint=FINGERPRINT,
        body="hello",
        available_at=available_at,
        created_at=T0,
    )


def test_round_trip_save_and_list_due(session: Session) -> None:
    run_id, invocation_id = _run_and_invocation(session)
    repo = OutboundDeliveryRepository(session)
    delivery = _delivery(run_id, invocation_id, T0)
    repo.add(delivery)
    session.flush()

    delivery.mark_sending(
        at=T0,
        claim_owner="worker",
        claim_token="token",
        claim_expires_at=T0 + timedelta(minutes=1),
    )
    delivery.mark_ambiguous(
        at=T0 + timedelta(seconds=1), failure_code="timeout", failure_message="lost"
    )
    repo.save(delivery)
    session.flush()
    fetched = repo.get(delivery.id)
    assert fetched is not None
    assert fetched.status is DeliveryStatus.AMBIGUOUS
    assert fetched.claim_token == "token"
    assert fetched.failure_code == "timeout"
    assert fetched.updated_at.tzinfo is UTC
    assert fetched.route_id == delivery.route_id
    assert fetched.route_fingerprint == delivery.route_fingerprint
    assert fetched.subject == delivery.subject
    assert fetched.body == delivery.body
    assert fetched.body_sha256 == delivery.body_sha256
    assert fetched.source_run_id == delivery.source_run_id
    assert fetched.source_tool_invocation_id == delivery.source_tool_invocation_id
    with pytest.raises(AttributeError):
        fetched.body = "retargeted"
    with pytest.raises(AttributeError):
        fetched.route_id = "retargeted.route"
    with pytest.raises(AttributeError):
        fetched.source_tool_invocation_id = ToolInvocationId.new()

    future_invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    ToolInvocationRepository(session).add(future_invocation)
    session.flush()
    future = _delivery(run_id, future_invocation.id, T0 + timedelta(minutes=1))
    repo.add(future)
    session.flush()
    assert repo.list_due(T0, 10) == []


def test_list_due_is_queued_only_and_deterministic(session: Session) -> None:
    run_id, invocation_a = _run_and_invocation(session)
    invocation_b = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="message.send",
        requested_input=None,
        requested_at=T0,
    )
    ToolInvocationRepository(session).add(invocation_b)
    session.flush()
    repo = OutboundDeliveryRepository(session)
    later = _delivery(run_id, invocation_a, T0 + timedelta(seconds=1))
    earlier = _delivery(run_id, invocation_b.id, T0)
    repo.add(later)
    repo.add(earlier)
    session.flush()
    assert [delivery.id for delivery in repo.list_due(T0 + timedelta(seconds=1), 1)] == [earlier.id]


def test_source_tool_invocation_is_unique(session: Session) -> None:
    run_id, invocation_id = _run_and_invocation(session)
    repo = OutboundDeliveryRepository(session)
    repo.add(_delivery(run_id, invocation_id, T0))
    session.flush()
    repo.add(_delivery(run_id, invocation_id, T0))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.flush()


def test_source_schedule_fire_is_unique(session: Session) -> None:
    run_id, _ = _run_and_invocation(session)
    run = session.get(RunRow, str(run_id))
    assert run is not None
    schedule_id = ScheduleId.new()
    fire_id = ScheduleFireId.new()
    session.add(
        ScheduleRow(
            id=str(schedule_id),
            task_id=run.task_id,
            kind="once",
            cron=None,
            run_at=T0,
            timezone="UTC",
            status="completed",
            next_fire_at=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    session.add(
        ScheduleFireRow(
            id=str(fire_id),
            schedule_id=str(schedule_id),
            scheduled_for=T0,
            fired_at=T0,
            run_id=str(run_id),
        )
    )
    session.flush()
    repo = OutboundDeliveryRepository(session)

    def scheduled() -> OutboundDelivery:
        return OutboundDelivery.new(
            id=DeliveryId.new(),
            source_kind=DeliverySourceKind.SCHEDULED_RUN_ANSWER,
            source_run_id=run_id,
            source_schedule_fire_id=fire_id,
            route_id="scheduled.route",
            route_fingerprint=FINGERPRINT,
            body="hello",
            available_at=T0,
            created_at=T0,
        )

    repo.add(scheduled())
    session.flush()
    repo.add(scheduled())
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        session.flush()

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

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
from friday.infrastructure.persistence.repositories import (
    OutboundDeliveryRepository,
    RunRepository,
    TaskRepository,
    ToolInvocationRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINGERPRINT = "a" * 64


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

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from friday.application.errors import ToolInputInvalid
from friday.application.tool_gateway import ToolCall, ToolExecutionRequest
from friday.domain.approval import ApprovalCategory
from friday.domain.identifiers import RunId, ToolInvocationId
from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes
from friday.infrastructure.messaging.message_tool_gateway import MessageToolGateway
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _gateway() -> tuple[MessageToolGateway, FakeUnitOfWork, FakeClock]:
    clock = FakeClock(T0)
    uow = FakeUnitOfWork()
    route = MessagingRoute(
        route_id="personal.notifications",
        trusted_description="Personal notification channel",
        principal_id="personal",
        endpoint="https://example.test/secret-webhook",
    )
    gateway = MessageToolGateway(CountingUnitOfWorkFactory(uow), clock, MessagingRoutes((route,)))
    return gateway, uow, clock


def _request(call: ToolCall) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        invocation_id=ToolInvocationId.new(), run_id=RunId.new(), step_id=None, call=call
    )


def test_message_tool_is_approval_protected_and_queues_without_endpoint() -> None:
    gateway, uow, _ = _gateway()
    call = ToolCall(
        tool="message.send", tool_input={"route": "personal.notifications", "body": "hello"}
    )
    risk = gateway.assess(call)
    result = gateway.execute(_request(call))

    assert risk.category is ApprovalCategory.EXTERNAL_COMMUNICATION
    assert risk.approval_required is True
    assert risk.authorization_scope is not None
    assert len(uow.delivery_repo.items) == 1
    delivery = next(iter(uow.delivery_repo.items.values()))
    assert result.output == {"delivery_id": str(delivery.id), "status": "queued"}
    assert "secret-webhook" not in repr(delivery)


def test_route_binding_and_deliver_at_are_validated() -> None:
    gateway, _, clock = _gateway()
    with pytest.raises(ToolInputInvalid, match="route is not configured"):
        gateway.assess(ToolCall(tool="message.send", tool_input={"route": "unknown", "body": "x"}))
    with pytest.raises(ToolInputInvalid, match="timezone"):
        gateway.assess(
            ToolCall(
                tool="message.send",
                tool_input={
                    "route": "personal.notifications",
                    "body": "x",
                    "deliver_at": "2026-01-02T00:00:00",
                },
            )
        )
    call = ToolCall(
        tool="message.send",
        tool_input={
            "route": "personal.notifications",
            "body": "x",
            "deliver_at": (clock.now() + timedelta(hours=1)).isoformat(),
        },
    )
    assert gateway.execute(_request(call)).output is not None


def test_route_fingerprint_changes_without_persisting_endpoint() -> None:
    first = MessagingRoute(
        route_id="personal.notifications",
        trusted_description="Personal notification channel",
        principal_id="personal",
        endpoint="https://example.test/a",
    )
    second = MessagingRoute(
        route_id="personal.notifications",
        trusted_description="Personal notification channel",
        principal_id="personal",
        endpoint="https://example.test/b",
    )
    assert first.fingerprint != second.fingerprint

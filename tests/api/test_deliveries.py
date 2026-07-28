from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from friday.domain.identifiers import DeliveryId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, OutboundDelivery
from friday.domain.tool import ToolInvocation
from friday.infrastructure.messaging.config import MessagingRoute, MessagingRoutes
from tests.api.conftest import NOW, seed_active_run


def test_delivery_status_is_safe_and_queued_delivery_can_be_cancelled(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=seeded.run_id,
        tool_name="message.send",
        requested_input={"route": "personal.notifications", "body": "secret body"},
        requested_at=NOW,
    )
    delivery = OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=seeded.run_id,
        source_tool_invocation_id=invocation.id,
        route_id="personal.notifications",
        route_fingerprint="a" * 64,
        body="secret body",
        available_at=NOW,
        created_at=NOW,
    )
    with app.state.uow_factory() as uow:
        uow.tool_invocations.add(invocation)
        uow.commit()
    with app.state.uow_factory() as uow:
        uow.deliveries.add(delivery)
        uow.commit()

    with TestClient(app) as client:
        fetched = client.get(f"/v1/deliveries/{delivery.id}")
        listed = client.get(f"/v1/runs/{seeded.run_id}/deliveries")
        cancelled = client.post(f"/v1/deliveries/{delivery.id}/cancel")
        repeat = client.post(f"/v1/deliveries/{delivery.id}/cancel")

    assert fetched.status_code == listed.status_code == cancelled.status_code == 200
    assert fetched.json()["route_id"] == "personal.notifications"
    assert "body" not in fetched.json()
    assert "secret body" not in str(fetched.json())
    assert listed.json()["items"][0]["id"] == str(delivery.id)
    assert cancelled.json()["status"] == "cancelled"
    assert repeat.status_code == 409


def test_messaging_route_health_is_safe_and_uses_the_latest_delivery(app: FastAPI) -> None:
    seeded = seed_active_run(app)
    app.state.messaging_routes = MessagingRoutes(
        (
            MessagingRoute(
                route_id="personal.notifications",
                trusted_description="Personal notifications",
                principal_id="operator",
                endpoint="https://example.test/credential-in-url",
            ),
        )
    )
    invocation = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=seeded.run_id,
        tool_name="message.send",
        requested_input={"route": "personal.notifications", "body": "message"},
        requested_at=NOW,
    )
    delivery = OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=seeded.run_id,
        source_tool_invocation_id=invocation.id,
        route_id="personal.notifications",
        route_fingerprint="a" * 64,
        body="message",
        available_at=NOW,
        created_at=NOW,
    )
    delivery.mark_sending(
        at=NOW,
        claim_owner="worker",
        claim_token="token",
        claim_expires_at=NOW.replace(year=NOW.year + 1),
    )
    delivery.fail(
        at=NOW,
        failure_code="route_unavailable",
        failure_message="credential-in-url must never appear in route health",
    )
    with app.state.uow_factory() as uow:
        uow.tool_invocations.add(invocation)
        uow.commit()
    with app.state.uow_factory() as uow:
        uow.deliveries.add(delivery)
        uow.commit()

    with TestClient(app) as client:
        response = client.get("/v1/messaging/routes")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item == {
        "route_id": "personal.notifications",
        "trusted_description": "Personal notifications",
        "transport": "webhook",
        "enabled": True,
        "status": "failed",
        "last_success_at": None,
        "failure_code": "route_unavailable",
    }
    assert "credential-in-url" not in response.text

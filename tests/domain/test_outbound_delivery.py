from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest

from friday.domain.errors import DomainValidationError, InvalidStateTransition
from friday.domain.identifiers import DeliveryId, RunId, ToolInvocationId
from friday.domain.outbound_delivery import DeliverySourceKind, DeliveryStatus, OutboundDelivery

T0 = datetime(2026, 1, 1, tzinfo=UTC)
FINGERPRINT = "a" * 64


def _delivery() -> OutboundDelivery:
    return OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id="personal.notifications",
        route_fingerprint=FINGERPRINT,
        subject="subject",
        body="hello",
        available_at=T0,
        created_at=T0,
    )


def test_new_delivery_is_queued_with_a_body_digest_and_utc_timestamps() -> None:
    delivery = _delivery()
    assert delivery.status == DeliveryStatus.QUEUED
    assert delivery.body_sha256 == hashlib.sha256(b"hello").hexdigest()
    assert delivery.attempt_count == 0

    offset_delivery = OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.AGENT_REQUEST,
        source_run_id=RunId.new(),
        source_tool_invocation_id=ToolInvocationId.new(),
        route_id="r",
        route_fingerprint=FINGERPRINT,
        body="b",
        available_at=datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7))),
        created_at=datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7))),
    )
    assert offset_delivery.created_at == T0


def test_delivery_state_machine_and_terminal_states() -> None:
    delivery = _delivery()
    delivery.mark_sending(
        at=T0, claim_owner="worker", claim_token="token", claim_expires_at=T0 + timedelta(minutes=1)
    )
    assert delivery.status == DeliveryStatus.SENDING
    assert (delivery.attempt_count, delivery.claim_generation) == (1, 1)
    delivery.deliver(at=T0 + timedelta(seconds=1), provider_message_id="provider-id")
    delivered_status: DeliveryStatus = delivery.status
    assert delivered_status == DeliveryStatus.DELIVERED
    with pytest.raises(InvalidStateTransition):
        delivery.mark_sending(
            at=T0,
            claim_owner="worker",
            claim_token="token",
            claim_expires_at=T0 + timedelta(minutes=1),
        )


def test_only_queued_delivery_can_be_cancelled_or_started() -> None:
    delivery = _delivery()
    delivery.cancel(at=T0)
    with pytest.raises(InvalidStateTransition):
        delivery.cancel(at=T0)

    sending = _delivery()
    sending.mark_sending(
        at=T0, claim_owner="worker", claim_token="token", claim_expires_at=T0 + timedelta(minutes=1)
    )
    with pytest.raises(InvalidStateTransition):
        sending.cancel(at=T0)


@pytest.mark.parametrize(
    ("field", "value"), [("route_id", ""), ("body", ""), ("route_fingerprint", "A" * 64)]
)
def test_new_validates_aggregate_owned_fields(field: str, value: str) -> None:
    kwargs = {field: value}
    with pytest.raises(DomainValidationError):
        OutboundDelivery.new(
            id=DeliveryId.new(),
            source_kind=DeliverySourceKind.AGENT_REQUEST,
            source_run_id=RunId.new(),
            source_tool_invocation_id=ToolInvocationId.new(),
            route_id=kwargs.get("route_id", "r"),
            route_fingerprint=kwargs.get("route_fingerprint", FINGERPRINT),
            body=kwargs.get("body", "b"),
            available_at=T0,
            created_at=T0,
        )

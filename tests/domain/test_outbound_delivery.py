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


def _claimed(at: datetime = T0) -> OutboundDelivery:
    delivery = _delivery()
    delivery.mark_sending(
        at=at, claim_owner="worker", claim_token="token", claim_expires_at=at + timedelta(minutes=1)
    )
    return delivery


def test_new_delivery_has_no_dispatch_boundary() -> None:
    assert _delivery().dispatch_started_at is None
    assert _claimed().dispatch_started_at is None


def test_dispatch_boundary_is_one_way_and_requires_an_unexpired_claim() -> None:
    delivery = _claimed()
    at = T0 + timedelta(seconds=5)
    delivery.mark_dispatch_started(at=at)
    assert delivery.dispatch_started_at == at
    assert delivery.status is DeliveryStatus.SENDING
    # One-way: the boundary is never crossed twice, moved, or cleared.
    with pytest.raises(InvalidStateTransition):
        delivery.mark_dispatch_started(at=at + timedelta(seconds=1))
    assert delivery.dispatch_started_at == at

    queued = _delivery()
    with pytest.raises(InvalidStateTransition):
        queued.mark_dispatch_started(at=T0)

    expired = _claimed()
    with pytest.raises(DomainValidationError):
        expired.mark_dispatch_started(at=T0 + timedelta(minutes=1))


def test_dispatch_started_at_rejects_direct_mutation() -> None:
    delivery = _claimed()
    with pytest.raises(AttributeError):
        delivery.dispatch_started_at = T0 + timedelta(seconds=1)
    assert delivery.dispatch_started_at is None

    delivery.mark_dispatch_started(at=T0 + timedelta(seconds=1))
    with pytest.raises(AttributeError):
        delivery.dispatch_started_at = T0 + timedelta(seconds=2)
    assert delivery.dispatch_started_at == T0 + timedelta(seconds=1)
    with pytest.raises(AttributeError):
        delivery.dispatch_started_at = None
    assert delivery.dispatch_started_at == T0 + timedelta(seconds=1)


def test_pre_dispatch_sending_may_be_released_for_retry() -> None:
    delivery = _claimed()
    at = T0 + timedelta(seconds=30)
    retry_at = at + timedelta(minutes=2)
    delivery.release_for_retry(at=at, available_at=retry_at)
    assert delivery.status is DeliveryStatus.QUEUED
    assert delivery.available_at == retry_at
    assert (delivery.claim_owner, delivery.claim_token, delivery.claim_expires_at) == (
        None,
        None,
        None,
    )
    # Retry budget and fencing history survive the requeue.
    assert (delivery.attempt_count, delivery.claim_generation) == (1, 1)

    delivery.mark_sending(
        at=retry_at,
        claim_owner="worker-2",
        claim_token="token-2",
        claim_expires_at=retry_at + timedelta(minutes=1),
    )
    assert (delivery.attempt_count, delivery.claim_generation) == (2, 2)


def test_post_dispatch_sending_can_never_be_released_for_retry() -> None:
    delivery = _claimed()
    delivery.mark_dispatch_started(at=T0 + timedelta(seconds=1))
    with pytest.raises(InvalidStateTransition):
        delivery.release_for_retry(
            at=T0 + timedelta(seconds=2), available_at=T0 + timedelta(minutes=5)
        )
    assert delivery.status is DeliveryStatus.SENDING


def test_release_for_retry_rejects_a_past_retry_time() -> None:
    delivery = _claimed()
    with pytest.raises(DomainValidationError):
        delivery.release_for_retry(at=T0 + timedelta(minutes=1), available_at=T0)


@pytest.mark.parametrize("outcome", ["deliver", "mark_ambiguous"])
def test_outcomes_that_imply_a_send_require_the_dispatch_boundary(outcome: str) -> None:
    delivery = _claimed()
    at = T0 + timedelta(seconds=1)
    with pytest.raises(InvalidStateTransition):
        if outcome == "deliver":
            delivery.deliver(at=at)
        else:
            delivery.mark_ambiguous(at=at, failure_code="timeout", failure_message="lost")
    assert delivery.status is DeliveryStatus.SENDING

    delivery.mark_dispatch_started(at=at)
    if outcome == "deliver":
        delivery.deliver(at=at + timedelta(seconds=1))
        expected = DeliveryStatus.DELIVERED
    else:
        delivery.mark_ambiguous(
            at=at + timedelta(seconds=1), failure_code="timeout", failure_message="lost"
        )
        expected = DeliveryStatus.AMBIGUOUS
    final_status: DeliveryStatus = delivery.status
    assert final_status is expected


def test_definite_pre_dispatch_failure_stays_available() -> None:
    """A pre-dispatch failure is definite: nothing was sent, so FAILED is honest."""
    delivery = _claimed()
    delivery.fail(at=T0 + timedelta(seconds=1), failure_code="route_denied", failure_message="no")
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.dispatch_started_at is None


@pytest.mark.parametrize("terminal", ["delivered", "failed", "ambiguous", "cancelled"])
def test_terminal_deliveries_can_never_return_to_queued(terminal: str) -> None:
    if terminal == "cancelled":
        delivery = _delivery()
        delivery.cancel(at=T0)
    else:
        delivery = _claimed()
        if terminal == "failed":
            delivery.fail(at=T0, failure_code="c", failure_message="m")
        else:
            delivery.mark_dispatch_started(at=T0 + timedelta(seconds=1))
            if terminal == "delivered":
                delivery.deliver(at=T0 + timedelta(seconds=2))
            else:
                delivery.mark_ambiguous(
                    at=T0 + timedelta(seconds=2), failure_code="c", failure_message="m"
                )

    with pytest.raises(InvalidStateTransition):
        delivery.release_for_retry(
            at=T0 + timedelta(minutes=1), available_at=T0 + timedelta(minutes=2)
        )
    with pytest.raises(InvalidStateTransition):
        delivery.mark_dispatch_started(at=T0 + timedelta(minutes=1))
    assert delivery.status is not DeliveryStatus.QUEUED


def test_delivery_state_machine_and_terminal_states() -> None:
    delivery = _delivery()
    delivery.mark_sending(
        at=T0, claim_owner="worker", claim_token="token", claim_expires_at=T0 + timedelta(minutes=1)
    )
    assert delivery.status == DeliveryStatus.SENDING
    assert (delivery.attempt_count, delivery.claim_generation) == (1, 1)
    delivery.mark_dispatch_started(at=T0 + timedelta(seconds=1))
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
    with pytest.raises(InvalidStateTransition):
        delivery.cancel(at=T0)


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
    ("field", "value"),
    [
        ("route_id", "other.route"),
        ("route_fingerprint", "b" * 64),
        ("subject", "other"),
        ("body", "other body"),
        ("body_sha256", "b" * 64),
        ("source_run_id", RunId.new()),
        ("source_tool_invocation_id", ToolInvocationId.new()),
    ],
)
def test_authority_and_content_are_immutable_after_creation(field: str, value: object) -> None:
    with pytest.raises(AttributeError):
        setattr(_delivery(), field, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("route_id", "other.route"),
        ("route_fingerprint", "b" * 64),
        ("subject", "other"),
        ("body", "other body"),
        ("body_sha256", "b" * 64),
        ("source_run_id", RunId.new()),
        ("source_tool_invocation_id", ToolInvocationId.new()),
    ],
)
def test_step_one_immutability_survives_the_dispatch_boundary(field: str, value: object) -> None:
    delivery = _claimed()
    delivery.mark_dispatch_started(at=T0 + timedelta(seconds=1))
    with pytest.raises(AttributeError):
        setattr(delivery, field, value)


def test_claim_does_not_allow_retargeting_authority_or_requeue() -> None:
    delivery = _delivery()
    delivery.claim(
        at=T0, claim_owner="worker", claim_token="token", claim_expires_at=T0 + timedelta(minutes=1)
    )
    with pytest.raises(AttributeError):
        delivery.route_id = "other.route"
    assert not hasattr(delivery, "requeue")
    delivery.fail(at=T0 + timedelta(seconds=1), failure_code="failed", failure_message="nope")
    assert delivery.status is DeliveryStatus.FAILED


def test_scheduled_delivery_source_identity_is_immutable() -> None:
    from friday.domain.identifiers import ScheduleFireId

    delivery = OutboundDelivery.new(
        id=DeliveryId.new(),
        source_kind=DeliverySourceKind.SCHEDULED_RUN_ANSWER,
        source_run_id=RunId.new(),
        source_schedule_fire_id=ScheduleFireId.new(),
        route_id="scheduled.route",
        route_fingerprint=FINGERPRINT,
        body="hello",
        available_at=T0,
        created_at=T0,
    )
    with pytest.raises(AttributeError):
        delivery.source_schedule_fire_id = ScheduleFireId.new()


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

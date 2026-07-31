from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunId, ScheduleFireDeliveryPlanId, ScheduleFireId, ScheduleId
from friday.domain.schedule_delivery_policy import ScheduleDeliveryPolicy
from friday.domain.schedule_fire_delivery_plan import (
    ScheduleFireDeliveryContentSource,
    ScheduleFireDeliveryPlan,
    ScheduleFireDeliveryPlanStatus,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_policy_is_a_guarded_authority_aggregate() -> None:
    policy = ScheduleDeliveryPolicy.new(
        schedule_id=ScheduleId.new(), route_id="ops.primary", enabled=False, now=NOW
    )
    with pytest.raises(AttributeError):
        policy._schedule_id = ScheduleId.new()  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(AttributeError):
        policy._created_at = NOW  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(AttributeError):
        policy._route_id = "other"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(AttributeError):
        policy._enabled = True  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(AttributeError):
        policy._updated_at = NOW  # pyright: ignore[reportAttributeAccessIssue]
    policy.update_route("ops.secondary", NOW + timedelta(seconds=1))
    policy.enable(NOW + timedelta(seconds=2))
    assert policy.route_id == "ops.secondary"
    assert policy.enabled is True


@pytest.mark.parametrize("route", ["Bad", "a..b", "x" * 65])
def test_policy_validates_route_and_boolean(route: str) -> None:
    with pytest.raises(DomainValidationError):
        ScheduleDeliveryPolicy.new(
            schedule_id=ScheduleId.new(), route_id=route, enabled=True, now=NOW
        )
    with pytest.raises(DomainValidationError):
        ScheduleDeliveryPolicy.new(
            schedule_id=ScheduleId.new(),
            route_id="ops",
            enabled=cast(bool, 1),
            now=NOW,
        )


def test_plan_accepts_only_exact_ready_and_suppressed_shapes() -> None:
    plan_id = ScheduleFireDeliveryPlanId.new()
    fire_id = ScheduleFireId.new()
    schedule_id = ScheduleId.new()
    execution_id = RunId.new()
    ready = ScheduleFireDeliveryPlan.ready(
        id=plan_id,
        schedule_fire_id=fire_id,
        schedule_id=schedule_id,
        execution_id=execution_id,
        route_id="ops",
        route_fingerprint="a" * 64,
        route_max_body_chars=16000,
        created_at=NOW,
    )
    assert ready.status is ScheduleFireDeliveryPlanStatus.READY
    suppressed = ScheduleFireDeliveryPlan.suppressed(
        id=plan_id,
        schedule_fire_id=fire_id,
        schedule_id=schedule_id,
        execution_id=execution_id,
        route_id="ops",
        reason_code="schedule_delivery_route_missing",
        created_at=NOW,
    )
    assert suppressed.status is ScheduleFireDeliveryPlanStatus.SUPPRESSED
    with pytest.raises(DomainValidationError):
        ScheduleFireDeliveryPlan(
            plan_id,
            fire_id,
            schedule_id,
            execution_id,
            "ops",
            route_fingerprint=None,
            route_max_body_chars=None,
            content_source=ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1,
            status="unknown",  # type: ignore[arg-type]
            reason_code="anything",
            content_rejected_run_id=None,
            created_at=NOW,
        )
    with pytest.raises(DomainValidationError):
        ScheduleFireDeliveryPlan(
            plan_id,
            fire_id,
            schedule_id,
            execution_id,
            "ops",
            route_fingerprint=None,
            route_max_body_chars=None,
            content_source="unknown",  # type: ignore[arg-type]
            status=ScheduleFireDeliveryPlanStatus.SUPPRESSED,
            reason_code="anything",
            content_rejected_run_id=None,
            created_at=NOW,
        )

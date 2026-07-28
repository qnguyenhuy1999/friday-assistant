from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.schemas.deliveries import DeliveryPageResponse, DeliveryResponse
from friday.application.errors import DeliveryNotFound, EntityConflict
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.identifiers import DeliveryId, RunId, ScheduleId
from friday.domain.outbound_delivery import OutboundDelivery

router = APIRouter(prefix="/v1", tags=["deliveries"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


def _delivery(value: OutboundDelivery) -> DeliveryResponse:
    return DeliveryResponse(
        id=str(value.id),
        source_kind=value.source_kind.value,
        source_run_id=str(value.source_run_id),
        source_schedule_fire_id=(
            str(value.source_schedule_fire_id) if value.source_schedule_fire_id else None
        ),
        route_id=value.route_id,
        status=value.status.value,
        available_at=value.available_at,
        attempt_count=value.attempt_count,
        failure_code=value.failure_code,
        created_at=value.created_at,
        updated_at=value.updated_at,
        delivered_at=value.delivered_at,
    )


@router.get(
    "/deliveries/{delivery_id}", response_model=DeliveryResponse, operation_id="getDelivery"
)
def get_delivery(delivery_id: UUID, uow_factory: UowDependency) -> DeliveryResponse:
    with uow_factory() as uow:
        parsed_id = DeliveryId.parse(str(delivery_id))
        result = uow.deliveries.get(parsed_id)
    if result is None:
        raise DeliveryNotFound(parsed_id)
    return _delivery(result)


@router.get(
    "/runs/{run_id}/deliveries",
    response_model=DeliveryPageResponse,
    operation_id="listRunDeliveries",
)
def list_run_deliveries(run_id: UUID, uow_factory: UowDependency) -> DeliveryPageResponse:
    with uow_factory() as uow:
        items = uow.deliveries.list_for_run(RunId.parse(str(run_id)))
    return DeliveryPageResponse(items=[_delivery(item) for item in items])


@router.get(
    "/schedules/{schedule_id}/deliveries",
    response_model=DeliveryPageResponse,
    operation_id="listScheduleDeliveries",
)
def list_schedule_deliveries(schedule_id: UUID, uow_factory: UowDependency) -> DeliveryPageResponse:
    with uow_factory() as uow:
        items = uow.deliveries.list_for_schedule(ScheduleId.parse(str(schedule_id)))
    return DeliveryPageResponse(items=[_delivery(item) for item in items])


@router.post(
    "/deliveries/{delivery_id}/cancel",
    response_model=DeliveryResponse,
    operation_id="cancelDelivery",
)
def cancel_delivery(
    delivery_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> DeliveryResponse:
    with uow_factory() as uow:
        result = uow.deliveries.cancel_if_queued(DeliveryId.parse(str(delivery_id)), clock.now())
        if result is None:
            raise EntityConflict("delivery cannot be cancelled after dispatch has started")
        uow.commit()
    return _delivery(result)

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_messaging_routes, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    page_from_query,
)
from apps.api.schemas.schedules import (
    CreateScheduleBody,
    ScheduleFirePageResponse,
    ScheduleFireResponse,
    SchedulePageResponse,
    ScheduleResponse,
    SetScheduleDeliveryBody,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.schedule_lifecycle import (
    ConfigureScheduleDelivery,
    CreateSchedule,
    CreateScheduleCommand,
    GetSchedule,
    ScheduleControl,
)
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.schedule_fire import ScheduleFire
from friday.domain.scheduled_delivery import ScheduleDeliveryPolicy
from friday.infrastructure.messaging.config import MessagingRoutes

router = APIRouter(prefix="/v1/tasks/{task_id}/schedules", tags=["schedules"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]
MessagingRoutesDependency = Annotated[MessagingRoutes, Depends(get_messaging_routes)]


def _schedule(
    value: Schedule,
    policy: ScheduleDeliveryPolicy | None = None,
    routes: MessagingRoutes | None = None,
) -> ScheduleResponse:
    route = routes.get(policy.route_id) if policy is not None and routes is not None else None
    return ScheduleResponse(
        id=str(value.id),
        task_id=str(value.task_id),
        kind=value.kind.value,
        cron=value.cron,
        run_at=value.run_at,
        timezone=value.timezone,
        status=value.status.value,
        next_fire_at=value.next_fire_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
        delivery_route_id=policy.route_id if policy is not None else None,
        delivery_route_description=route.trusted_description if route is not None else None,
        delivery_enabled=route.enabled if route is not None else None,
    )


def _fire(value: ScheduleFire) -> ScheduleFireResponse:
    return ScheduleFireResponse(
        id=str(value.id),
        schedule_id=str(value.schedule_id),
        scheduled_for=value.scheduled_for,
        fired_at=value.fired_at,
        run_id=str(value.run_id),
    )


@router.post(
    "",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSchedule",
)
def create_schedule(
    task_id: UUID,
    body: CreateScheduleBody,
    uow_factory: UowDependency,
    clock: ClockDependency,
    messaging_routes: MessagingRoutesDependency,
) -> ScheduleResponse:
    route = (
        messaging_routes.get_enabled(body.delivery_route_id)
        if body.delivery_route_id is not None
        else None
    )
    if body.delivery_route_id is not None and route is None:
        from friday.application.errors import EntityConflict

        raise EntityConflict("delivery route is not configured")
    result = CreateSchedule(uow_factory, clock).execute(
        CreateScheduleCommand(
            task_id=TaskId.parse(str(task_id)),
            kind=ScheduleKind(body.kind),
            cron=body.cron,
            run_at=body.run_at,
            timezone=body.timezone,
            delivery_route_id=route.route_id if route is not None else None,
            delivery_route_fingerprint=route.fingerprint if route is not None else None,
        )
    )
    with uow_factory() as uow:
        policy = uow.schedule_delivery_policies.get(result.id)
    return _schedule(result, policy, messaging_routes)


@router.get("", response_model=SchedulePageResponse, operation_id="listSchedules")
def list_schedules(
    task_id: UUID,
    uow_factory: UowDependency,
    messaging_routes: MessagingRoutesDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> SchedulePageResponse:
    after = decode_cursor(
        cursor, collection="schedules", parent_id=str(task_id), order="created_at_id_asc", parts=2
    )
    with uow_factory() as uow:
        items = uow.schedules.list_for_task_page(
            TaskId.parse(str(task_id)),
            limit + 1,
            cursor_datetime(after.after[0]) if after else None,
            after.after[1] if after else None,
        )
        policies = {item.id: uow.schedule_delivery_policies.get(item.id) for item in items}
    page, next_cursor = page_from_query(
        items,
        limit=limit,
        collection="schedules",
        parent_id=str(task_id),
        order="created_at_id_asc",
        key=lambda value: (value.created_at.isoformat(), str(value.id)),
    )
    return SchedulePageResponse(
        items=[_schedule(item, policies[item.id], messaging_routes) for item in page],
        next_cursor=next_cursor,
    )


@router.get("/{schedule_id}", response_model=ScheduleResponse, operation_id="getSchedule")
def get_schedule(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UowDependency,
    messaging_routes: MessagingRoutesDependency,
) -> ScheduleResponse:
    result = GetSchedule(uow_factory).execute(
        ScheduleId.parse(str(schedule_id)), task_id=TaskId.parse(str(task_id))
    )
    with uow_factory() as uow:
        policy = uow.schedule_delivery_policies.get(result.id)
    return _schedule(result, policy, messaging_routes)


@router.post(
    "/{schedule_id}/delivery",
    response_model=ScheduleResponse,
    operation_id="setScheduleDelivery",
)
def set_schedule_delivery(
    task_id: UUID,
    schedule_id: UUID,
    body: SetScheduleDeliveryBody,
    uow_factory: UowDependency,
    clock: ClockDependency,
    messaging_routes: MessagingRoutesDependency,
) -> ScheduleResponse:
    route = (
        messaging_routes.get_enabled(body.delivery_route_id)
        if body.delivery_route_id is not None
        else None
    )
    if body.delivery_route_id is not None and route is None:
        from friday.application.errors import EntityConflict

        raise EntityConflict("delivery route is not configured")
    result = ConfigureScheduleDelivery(uow_factory, clock).execute(
        ScheduleId.parse(str(schedule_id)),
        task_id=TaskId.parse(str(task_id)),
        route_id=route.route_id if route is not None else None,
        route_fingerprint=route.fingerprint if route is not None else None,
    )
    with uow_factory() as uow:
        policy = uow.schedule_delivery_policies.get(result.id)
    return _schedule(result, policy, messaging_routes)


def _control(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    action: str,
    messaging_routes: MessagingRoutes,
) -> ScheduleResponse:
    control = ScheduleControl(uow_factory, clock)
    result = getattr(control, action)(
        ScheduleId.parse(str(schedule_id)), task_id=TaskId.parse(str(task_id))
    )
    with uow_factory() as uow:
        policy = uow.schedule_delivery_policies.get(result.id)
    return _schedule(result, policy, messaging_routes)


@router.post("/{schedule_id}/pause", response_model=ScheduleResponse, operation_id="pauseSchedule")
def pause_schedule(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UowDependency,
    clock: ClockDependency,
    messaging_routes: MessagingRoutesDependency,
) -> ScheduleResponse:
    return _control(task_id, schedule_id, uow_factory, clock, "pause", messaging_routes)


@router.post(
    "/{schedule_id}/resume", response_model=ScheduleResponse, operation_id="resumeSchedule"
)
def resume_schedule(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UowDependency,
    clock: ClockDependency,
    messaging_routes: MessagingRoutesDependency,
) -> ScheduleResponse:
    return _control(task_id, schedule_id, uow_factory, clock, "resume", messaging_routes)


@router.post(  # noqa: E501
    "/{schedule_id}/cancel", response_model=ScheduleResponse, operation_id="cancelSchedule"
)
def cancel_schedule(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UowDependency,
    clock: ClockDependency,
    messaging_routes: MessagingRoutesDependency,
) -> ScheduleResponse:
    return _control(task_id, schedule_id, uow_factory, clock, "cancel", messaging_routes)


@router.get(
    "/{schedule_id}/fires",
    response_model=ScheduleFirePageResponse,
    operation_id="listScheduleFires",
)
def list_fires(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UowDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> ScheduleFirePageResponse:
    schedule = GetSchedule(uow_factory).execute(
        ScheduleId.parse(str(schedule_id)), task_id=TaskId.parse(str(task_id))
    )
    after = decode_cursor(
        cursor,
        collection="schedule_fires",
        parent_id=str(schedule_id),
        order="scheduled_for_id_asc",
        parts=2,
    )
    with uow_factory() as uow:
        items = uow.schedule_fires.list_for_schedule_page(
            schedule.id,
            limit + 1,
            cursor_datetime(after.after[0]) if after else None,
            after.after[1] if after else None,
        )
    page, next_cursor = page_from_query(
        items,
        limit=limit,
        collection="schedule_fires",
        parent_id=str(schedule_id),
        order="scheduled_for_id_asc",
        key=lambda value: (value.scheduled_for.isoformat(), str(value.id)),
    )
    return ScheduleFirePageResponse(items=[_fire(item) for item in page], next_cursor=next_cursor)

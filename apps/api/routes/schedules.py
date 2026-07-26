from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
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
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.schedule_lifecycle import (
    CreateSchedule,
    CreateScheduleCommand,
    GetSchedule,
    ScheduleControl,
)
from friday.domain.identifiers import ScheduleId, TaskId
from friday.domain.schedule import Schedule, ScheduleKind
from friday.domain.schedule_fire import ScheduleFire

router = APIRouter(prefix="/v1/tasks/{task_id}/schedules", tags=["schedules"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


def _schedule(value: Schedule) -> ScheduleResponse:
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
) -> ScheduleResponse:
    result = CreateSchedule(uow_factory, clock).execute(
        CreateScheduleCommand(
            task_id=TaskId.parse(str(task_id)),
            kind=ScheduleKind(body.kind),
            cron=body.cron,
            run_at=body.run_at,
            timezone=body.timezone,
        )
    )
    return _schedule(result)


@router.get("", response_model=SchedulePageResponse, operation_id="listSchedules")
def list_schedules(
    task_id: UUID,
    uow_factory: UowDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> SchedulePageResponse:
    items = GetSchedule(uow_factory).list_for_task(TaskId.parse(str(task_id)), limit + 1)
    page, next_cursor = page_from_query(
        items,
        limit=limit,
        collection="schedules",
        parent_id=str(task_id),
        order="created_at_id_asc",
        key=lambda value: (value.created_at.isoformat(), str(value.id)),
    )
    return SchedulePageResponse(items=[_schedule(item) for item in page], next_cursor=next_cursor)


@router.get("/{schedule_id}", response_model=ScheduleResponse, operation_id="getSchedule")
def get_schedule(task_id: UUID, schedule_id: UUID, uow_factory: UowDependency) -> ScheduleResponse:
    result = GetSchedule(uow_factory).execute(ScheduleId.parse(str(schedule_id)))
    if result.task_id != TaskId.parse(str(task_id)):
        raise ValueError("schedule does not belong to task")
    return _schedule(result)


def _control(
    task_id: UUID,
    schedule_id: UUID,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    action: str,
) -> ScheduleResponse:
    control = ScheduleControl(uow_factory, clock)
    result = getattr(control, action)(
        ScheduleId.parse(str(schedule_id)), task_id=TaskId.parse(str(task_id))
    )
    return _schedule(result)


@router.post("/{schedule_id}/pause", response_model=ScheduleResponse, operation_id="pauseSchedule")
def pause_schedule(
    task_id: UUID, schedule_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> ScheduleResponse:
    return _control(task_id, schedule_id, uow_factory, clock, "pause")


@router.post(
    "/{schedule_id}/resume", response_model=ScheduleResponse, operation_id="resumeSchedule"
)
def resume_schedule(
    task_id: UUID, schedule_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> ScheduleResponse:
    return _control(task_id, schedule_id, uow_factory, clock, "resume")


@router.post(  # noqa: E501
    "/{schedule_id}/cancel", response_model=ScheduleResponse, operation_id="cancelSchedule"
)
def cancel_schedule(
    task_id: UUID, schedule_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> ScheduleResponse:
    return _control(task_id, schedule_id, uow_factory, clock, "cancel")


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
    schedule = GetSchedule(uow_factory).execute(ScheduleId.parse(str(schedule_id)))
    if schedule.task_id != TaskId.parse(str(task_id)):
        raise ValueError("schedule does not belong to task")
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

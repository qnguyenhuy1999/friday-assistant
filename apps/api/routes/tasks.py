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
from apps.api.schemas.tasks import (
    CreateTaskBody,
    FailureBody,
    StartRunResponse,
    TaskPageResponse,
    TaskResponse,
)
from friday.application.commands import (
    CancelTaskCommand,
    CompleteTaskCommand,
    CreateTaskCommand,
    FailTaskCommand,
    StartRunCommand,
)
from friday.application.create_task import CreateTask
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import TaskResult
from friday.application.start_run import StartRun
from friday.application.task_lifecycle import CancelTask, CompleteTask, FailTask, GetTask, ListTasks
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import TaskId

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


def _task_response(result: TaskResult) -> TaskResponse:
    failure = (
        FailureBody(
            code=result.failure.code,
            message=result.failure.message,
            retryable=result.failure.retryable,
            cause=result.failure.cause,
            details=result.failure.details,
        )
        if result.failure is not None
        else None
    )
    return TaskResponse(
        id=str(result.task_id),
        title=result.title,
        description=result.description,
        status=result.status,
        created_at=result.created_at,
        failure=failure,
    )


def _failure(body: FailureBody) -> Failure:
    return Failure(body.code, body.message, body.retryable, FailureCause(body.cause), body.details)


@router.post(
    "", response_model=TaskResponse, status_code=status.HTTP_201_CREATED, operation_id="createTask"
)
def create_task(
    body: CreateTaskBody, uow_factory: UowDependency, clock: ClockDependency
) -> TaskResponse:
    created = CreateTask(uow_factory, clock).execute(
        CreateTaskCommand(title=body.title, description=body.description)
    )
    return _task_response(GetTask(uow_factory, clock).execute(created.task_id))


@router.get("", response_model=TaskPageResponse, operation_id="listTasks")
def list_tasks(
    uow_factory: UowDependency,
    clock: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> TaskPageResponse:
    after = decode_cursor(
        cursor, collection="tasks", parent_id=None, order="created_at_id_asc", parts=2
    )
    results = ListTasks(uow_factory, clock).page(
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="tasks",
        parent_id=None,
        order="created_at_id_asc",
        key=lambda task: (task.created_at.isoformat(), str(task.task_id)),
    )
    return TaskPageResponse(items=[_task_response(item) for item in page], next_cursor=next_cursor)


@router.get("/{task_id}", response_model=TaskResponse, operation_id="getTask")
def get_task(task_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> TaskResponse:
    return _task_response(GetTask(uow_factory, clock).execute(TaskId.parse(str(task_id))))


@router.post(
    "/{task_id}/runs",
    response_model=StartRunResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="startRun",
)
def start_run(
    task_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> StartRunResponse:
    result = StartRun(uow_factory, clock).execute(StartRunCommand(TaskId.parse(str(task_id))))
    return StartRunResponse(task_id=str(result.task_id), run_id=str(result.run_id))


@router.post("/{task_id}/cancel", response_model=TaskResponse, operation_id="cancelTask")
def cancel_task(task_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> TaskResponse:
    return _task_response(
        CancelTask(uow_factory, clock).execute(CancelTaskCommand(TaskId.parse(str(task_id))))
    )


@router.post("/{task_id}/complete", response_model=TaskResponse, operation_id="completeTask")
def complete_task(
    task_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> TaskResponse:
    return _task_response(
        CompleteTask(uow_factory, clock).execute(CompleteTaskCommand(TaskId.parse(str(task_id))))
    )


@router.post("/{task_id}/fail", response_model=TaskResponse, operation_id="failTask")
def fail_task(
    task_id: UUID, body: FailureBody, uow_factory: UowDependency, clock: ClockDependency
) -> TaskResponse:
    return _task_response(
        FailTask(uow_factory, clock).execute(
            FailTaskCommand(TaskId.parse(str(task_id)), _failure(body))
        )
    )

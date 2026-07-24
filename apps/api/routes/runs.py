from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    page_from_query,
)
from apps.api.schemas.runs import RunPageResponse, RunResponse
from apps.api.schemas.tasks import FailureBody
from friday.application.commands import (
    CancelRunCommand,
    CompleteRunCommand,
    FailRunCommand,
    RetryFailedRunCommand,
    StartQueuedRunCommand,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import RunResult
from friday.application.run_lifecycle import (
    CancelRun,
    CompleteRun,
    FailRun,
    GetRun,
    ListRunsForTask,
    RetryFailedRun,
    StartQueuedRun,
)
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunId, TaskId

router = APIRouter(prefix="/v1", tags=["runs"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


def _run_response(result: RunResult) -> RunResponse:
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
    return RunResponse(
        id=str(result.run_id),
        task_id=str(result.task_id),
        status=result.status,
        created_at=result.created_at,
        failure=failure,
    )


def _failure(body: FailureBody) -> Failure:
    return Failure(body.code, body.message, body.retryable, FailureCause(body.cause), body.details)


@router.get("/runs/{run_id}", response_model=RunResponse, operation_id="getRun")
def get_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(GetRun(uow_factory, clock).execute(RunId.parse(str(run_id))))


@router.get("/tasks/{task_id}/runs", response_model=RunPageResponse, operation_id="listRunsForTask")
def list_runs(
    task_id: UUID,
    uow_factory: UowDependency,
    clock: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> RunPageResponse:
    parent_id = str(task_id)
    after = decode_cursor(
        cursor, collection="task_runs", parent_id=parent_id, order="created_at_id_asc", parts=2
    )
    results = ListRunsForTask(uow_factory, clock).page(
        TaskId.parse(parent_id),
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="task_runs",
        parent_id=parent_id,
        order="created_at_id_asc",
        key=lambda run: (run.created_at.isoformat(), str(run.run_id)),
    )
    return RunPageResponse(items=[_run_response(item) for item in page], next_cursor=next_cursor)


@router.post("/runs/{run_id}/start", response_model=RunResponse, operation_id="startQueuedRun")
def start_queued_run(
    run_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> RunResponse:
    return _run_response(
        StartQueuedRun(uow_factory, clock).execute(StartQueuedRunCommand(RunId.parse(str(run_id))))
    )


@router.post("/runs/{run_id}/complete", response_model=RunResponse, operation_id="completeRun")
def complete_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(
        CompleteRun(uow_factory, clock).execute(CompleteRunCommand(RunId.parse(str(run_id))))
    )


@router.post("/runs/{run_id}/fail", response_model=RunResponse, operation_id="failRun")
def fail_run(
    run_id: UUID, body: FailureBody, uow_factory: UowDependency, clock: ClockDependency
) -> RunResponse:
    return _run_response(
        FailRun(uow_factory, clock).execute(
            FailRunCommand(RunId.parse(str(run_id)), _failure(body))
        )
    )


@router.post("/runs/{run_id}/cancel", response_model=RunResponse, operation_id="cancelRun")
def cancel_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(
        CancelRun(uow_factory, clock).execute(CancelRunCommand(RunId.parse(str(run_id))))
    )


@router.post("/runs/{run_id}/retry", response_model=RunResponse, operation_id="retryFailedRun")
def retry_run(run_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> RunResponse:
    return _run_response(
        RetryFailedRun(uow_factory, clock).execute(RetryFailedRunCommand(RunId.parse(str(run_id))))
    )

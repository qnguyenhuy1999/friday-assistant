from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, page_ordered
from apps.api.schemas.steps import CreateStepBody, StepPageResponse, StepResponse
from apps.api.schemas.tasks import FailureBody
from friday.application.commands import (
    CancelStepCommand,
    CompleteStepCommand,
    CreateOrderedStepCommand,
    FailStepCommand,
    SkipPendingStepCommand,
    StartStepCommand,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.results import RunStepResult
from friday.application.run_step_lifecycle import (
    CancelStep,
    CompleteStep,
    CreateOrderedStep,
    FailStep,
    GetRunStep,
    ListRunStepsForRun,
    SkipPendingStep,
    StartStep,
)
from friday.domain.failure import Failure, FailureCause
from friday.domain.identifiers import RunId, RunStepId

router = APIRouter(prefix="/v1", tags=["run-steps"])
UowDependency = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
ClockDependency = Annotated[Clock, Depends(get_clock)]


def _step_response(result: RunStepResult) -> StepResponse:
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
    return StepResponse(
        id=str(result.step_id),
        run_id=str(result.run_id),
        name=result.name,
        position=result.position,
        status=result.status,
        failure=failure,
    )


def _failure(body: FailureBody) -> Failure:
    return Failure(body.code, body.message, body.retryable, FailureCause(body.cause), body.details)


@router.post(
    "/runs/{run_id}/steps",
    response_model=StepResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOrderedStep",
)
def create_step(
    run_id: UUID, body: CreateStepBody, uow_factory: UowDependency, clock: ClockDependency
) -> StepResponse:
    command = CreateOrderedStepCommand(RunId.parse(str(run_id)), body.name)
    return _step_response(CreateOrderedStep(uow_factory, clock).execute(command))


@router.get(
    "/runs/{run_id}/steps", response_model=StepPageResponse, operation_id="listRunStepsForRun"
)
def list_steps(
    run_id: UUID,
    uow_factory: UowDependency,
    clock: ClockDependency,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> StepPageResponse:
    results = ListRunStepsForRun(uow_factory, clock).execute(RunId.parse(str(run_id)))
    page, next_cursor = page_ordered(
        results,
        limit=limit,
        cursor=cursor,
        key=lambda step: (str(step.position), str(step.step_id)),
    )
    return StepPageResponse(items=[_step_response(item) for item in page], next_cursor=next_cursor)


@router.get("/steps/{step_id}", response_model=StepResponse, operation_id="getRunStep")
def get_step(step_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> StepResponse:
    return _step_response(GetRunStep(uow_factory, clock).execute(RunStepId.parse(str(step_id))))


@router.post("/steps/{step_id}/start", response_model=StepResponse, operation_id="startStep")
def start_step(step_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> StepResponse:
    return _step_response(
        StartStep(uow_factory, clock).execute(StartStepCommand(RunStepId.parse(str(step_id))))
    )


@router.post("/steps/{step_id}/complete", response_model=StepResponse, operation_id="completeStep")
def complete_step(
    step_id: UUID, uow_factory: UowDependency, clock: ClockDependency
) -> StepResponse:
    return _step_response(
        CompleteStep(uow_factory, clock).execute(CompleteStepCommand(RunStepId.parse(str(step_id))))
    )


@router.post("/steps/{step_id}/fail", response_model=StepResponse, operation_id="failStep")
def fail_step(
    step_id: UUID, body: FailureBody, uow_factory: UowDependency, clock: ClockDependency
) -> StepResponse:
    return _step_response(
        FailStep(uow_factory, clock).execute(
            FailStepCommand(RunStepId.parse(str(step_id)), _failure(body))
        )
    )


@router.post("/steps/{step_id}/skip", response_model=StepResponse, operation_id="skipPendingStep")
def skip_step(step_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> StepResponse:
    return _step_response(
        SkipPendingStep(uow_factory, clock).execute(
            SkipPendingStepCommand(RunStepId.parse(str(step_id)))
        )
    )


@router.post("/steps/{step_id}/cancel", response_model=StepResponse, operation_id="cancelStep")
def cancel_step(step_id: UUID, uow_factory: UowDependency, clock: ClockDependency) -> StepResponse:
    return _step_response(
        CancelStep(uow_factory, clock).execute(CancelStepCommand(RunStepId.parse(str(step_id))))
    )

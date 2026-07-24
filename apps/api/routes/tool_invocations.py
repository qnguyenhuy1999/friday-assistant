from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from apps.api.dependencies import get_clock, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_datetime,
    decode_cursor,
    page_from_query,
)
from apps.api.schemas.tool_invocations import (
    MarkFailedBody,
    MarkSucceededBody,
    RequestToolInvocationBody,
    ToolInvocationPage,
    ToolInvocationResponse,
)
from friday.application.commands import (
    CancelToolInvocationCommand,
    MarkToolInvocationRunningCommand,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.application.tool_invocation_lifecycle import (
    CancelToolInvocation,
    GetToolInvocation,
    ListToolInvocationsForRun,
    ListToolInvocationsForStep,
    MarkToolInvocationFailed,
    MarkToolInvocationRunning,
    MarkToolInvocationSucceeded,
    RequestToolInvocation,
)
from friday.domain.identifiers import RunId, RunStepId, ToolInvocationId

router = APIRouter(tags=["tool-invocations"])


@router.post(
    "/v1/runs/{run_id}/tool-invocations",
    operation_id="requestToolInvocation",
    status_code=status.HTTP_201_CREATED,
)
def request_tool_invocation(
    run_id: str,
    body: RequestToolInvocationBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ToolInvocationResponse:
    result = RequestToolInvocation(uow_factory, clock).execute(body.to_command(RunId.parse(run_id)))
    return ToolInvocationResponse.from_result(result)


@router.get("/v1/tool-invocations/{invocation_id}", operation_id="getToolInvocation")
def get_tool_invocation(
    invocation_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ToolInvocationResponse:
    result = GetToolInvocation(uow_factory, clock).execute(ToolInvocationId.parse(invocation_id))
    return ToolInvocationResponse.from_result(result)


def _tool_invocation_key(result: object) -> tuple[str, ...]:
    return (result.requested_at.isoformat(), str(result.invocation_id))  # type: ignore[attr-defined]


@router.get("/v1/runs/{run_id}/tool-invocations", operation_id="listToolInvocationsForRun")
def list_tool_invocations_for_run(
    run_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> ToolInvocationPage:
    after = decode_cursor(
        cursor,
        collection="run_tool_invocations",
        parent_id=run_id,
        order="requested_at_id_asc",
        parts=2,
    )
    results = ListToolInvocationsForRun(uow_factory, clock).page(
        RunId.parse(run_id),
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="run_tool_invocations",
        parent_id=run_id,
        order="requested_at_id_asc",
        key=_tool_invocation_key,
    )
    return ToolInvocationPage(
        items=[ToolInvocationResponse.from_result(r) for r in page], next_cursor=next_cursor
    )


@router.get("/v1/steps/{step_id}/tool-invocations", operation_id="listToolInvocationsForStep")
def list_tool_invocations_for_step(
    step_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> ToolInvocationPage:
    after = decode_cursor(
        cursor,
        collection="step_tool_invocations",
        parent_id=step_id,
        order="requested_at_id_asc",
        parts=2,
    )
    results = ListToolInvocationsForStep(uow_factory, clock).page(
        RunStepId.parse(step_id),
        limit + 1,
        cursor_datetime(after.after[0]) if after else None,
        after.after[1] if after else None,
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="step_tool_invocations",
        parent_id=step_id,
        order="requested_at_id_asc",
        key=_tool_invocation_key,
    )
    return ToolInvocationPage(
        items=[ToolInvocationResponse.from_result(r) for r in page], next_cursor=next_cursor
    )


@router.post(
    "/v1/tool-invocations/{invocation_id}/running", operation_id="markToolInvocationRunning"
)
def mark_tool_invocation_running(
    invocation_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ToolInvocationResponse:
    command = MarkToolInvocationRunningCommand(invocation_id=ToolInvocationId.parse(invocation_id))
    result = MarkToolInvocationRunning(uow_factory, clock).execute(command)
    return ToolInvocationResponse.from_result(result)


@router.post(
    "/v1/tool-invocations/{invocation_id}/succeed", operation_id="markToolInvocationSucceeded"
)
def mark_tool_invocation_succeeded(
    invocation_id: str,
    body: MarkSucceededBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ToolInvocationResponse:
    command = body.to_command(ToolInvocationId.parse(invocation_id))
    result = MarkToolInvocationSucceeded(uow_factory, clock).execute(command)
    return ToolInvocationResponse.from_result(result)


@router.post("/v1/tool-invocations/{invocation_id}/fail", operation_id="markToolInvocationFailed")
def mark_tool_invocation_failed(
    invocation_id: str,
    body: MarkFailedBody,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ToolInvocationResponse:
    command = body.to_command(ToolInvocationId.parse(invocation_id))
    result = MarkToolInvocationFailed(uow_factory, clock).execute(command)
    return ToolInvocationResponse.from_result(result)


@router.post("/v1/tool-invocations/{invocation_id}/cancel", operation_id="cancelToolInvocation")
def cancel_tool_invocation(
    invocation_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> ToolInvocationResponse:
    command = CancelToolInvocationCommand(invocation_id=ToolInvocationId.parse(invocation_id))
    result = CancelToolInvocation(uow_factory, clock).execute(command)
    return ToolInvocationResponse.from_result(result)

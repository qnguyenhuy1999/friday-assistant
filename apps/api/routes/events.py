"""RunEvent/TaskEvent read endpoints, plus a Server-Sent-Events stream of
committed RunEvents. The SSE endpoint never holds a UnitOfWork open across
the connection -- each poll opens, reads, and closes one via `ListRunEvents`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from apps.api.dependencies import get_clock, get_settings, get_uow_factory
from apps.api.pagination import CursorQuery, LimitQuery, paginate
from apps.api.schemas.events import RunEventPage, RunEventResponse, TaskEventPage
from apps.api.settings import ApiSettings
from friday.application.list_events import ListRunEvents, ListTaskEvents
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.identifiers import RunId, TaskId

router = APIRouter(tags=["events"])


@router.get("/v1/runs/{run_id}/events", operation_id="listRunEvents")
def list_run_events(
    run_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> RunEventPage:
    results = ListRunEvents(uow_factory, clock).execute(RunId.parse(run_id))
    return RunEventPage.from_page(paginate(results, cursor=cursor, limit=limit))


@router.get("/v1/tasks/{task_id}/events", operation_id="listTaskEvents")
def list_task_events(
    task_id: str,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    cursor: CursorQuery = None,
    limit: LimitQuery = 20,
) -> TaskEventPage:
    results = ListTaskEvents(uow_factory, clock).execute(TaskId.parse(task_id))
    return TaskEventPage.from_page(paginate(results, cursor=cursor, limit=limit))


def _parse_last_event_id(value: str | None) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="malformed Last-Event-ID",
        ) from None
    if parsed < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="malformed Last-Event-ID",
        )
    return parsed


async def _run_event_stream(
    request: Request,
    run_id: RunId,
    uow_factory: UnitOfWorkFactory,
    clock: Clock,
    last_seen: int,
    poll_interval: float,
) -> AsyncIterator[str]:
    while True:
        if await request.is_disconnected():
            return
        events = ListRunEvents(uow_factory, clock).execute(run_id)
        for event in events:
            if event.sequence <= last_seen:
                continue
            last_seen = event.sequence
            data = RunEventResponse.from_domain(event).model_dump_json()
            yield f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n"
        await asyncio.sleep(poll_interval)


@router.get("/v1/runs/{run_id}/events/stream", operation_id="streamRunEvents")
async def stream_run_events(
    run_id: str,
    request: Request,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    parsed_run_id = RunId.parse(run_id)
    last_seen = _parse_last_event_id(last_event_id)
    ListRunEvents(uow_factory, clock).execute(parsed_run_id)
    return StreamingResponse(
        _run_event_stream(
            request,
            parsed_run_id,
            uow_factory,
            clock,
            last_seen,
            settings.sse_poll_interval_seconds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )

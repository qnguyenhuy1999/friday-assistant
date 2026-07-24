"""RunEvent/TaskEvent read endpoints, plus a Server-Sent-Events stream of
committed RunEvents. The SSE endpoint never holds a UnitOfWork open across
the connection -- each poll opens, reads, and closes one via `ListRunEvents`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from apps.api.dependencies import get_clock, get_settings, get_uow_factory
from apps.api.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    cursor_int,
    decode_cursor,
    page_from_query,
)
from apps.api.schemas.events import RunEventPage, RunEventResponse, TaskEventPage, TaskEventResponse
from apps.api.settings import ApiSettings
from friday.application.list_events import ListRunEvents, ListTaskEvents
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain.event import RunEvent
from friday.domain.identifiers import RunId, TaskId

router = APIRouter(tags=["events"])


@router.get("/v1/runs/{run_id}/events", operation_id="listRunEvents")
def list_run_events(
    run_id: UUID,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> RunEventPage:
    parent_id = str(run_id)
    after = decode_cursor(
        cursor, collection="run_events", parent_id=parent_id, order="sequence_asc", parts=1
    )
    results = ListRunEvents(uow_factory, clock).after(
        RunId.parse(parent_id), cursor_int(after.after[0]) if after else 0, limit + 1
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="run_events",
        parent_id=parent_id,
        order="sequence_asc",
        key=lambda e: (e.sequence,),
    )
    return RunEventPage(
        items=[RunEventResponse.from_domain(e) for e in page], next_cursor=next_cursor
    )


@router.get("/v1/tasks/{task_id}/events", operation_id="listTaskEvents")
def list_task_events(
    task_id: UUID,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> TaskEventPage:
    parent_id = str(task_id)
    after = decode_cursor(
        cursor, collection="task_events", parent_id=parent_id, order="sequence_asc", parts=1
    )
    results = ListTaskEvents(uow_factory, clock).after(
        TaskId.parse(parent_id), cursor_int(after.after[0]) if after else 0, limit + 1
    )
    page, next_cursor = page_from_query(
        results,
        limit=limit,
        collection="task_events",
        parent_id=parent_id,
        order="sequence_asc",
        key=lambda e: (e.sequence,),
    )
    return TaskEventPage(
        items=[TaskEventResponse.from_domain(e) for e in page], next_cursor=next_cursor
    )


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
        after_sequence = last_seen

        def read_page(sequence: int = after_sequence) -> list[RunEvent]:
            return ListRunEvents(uow_factory, clock).after(run_id, sequence, 100)

        events = await anyio.to_thread.run_sync(read_page)
        for event in events:
            if event.sequence <= last_seen:
                continue
            last_seen = event.sequence
            data = RunEventResponse.from_domain(event).model_dump_json()
            yield f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n"
        await asyncio.sleep(poll_interval)


@router.get("/v1/runs/{run_id}/events/stream", operation_id="streamRunEvents")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    uow_factory: Annotated[UnitOfWorkFactory, Depends(get_uow_factory)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[ApiSettings, Depends(get_settings)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    parsed_run_id = RunId.parse(str(run_id))
    last_seen = _parse_last_event_id(last_event_id)
    await anyio.to_thread.run_sync(
        lambda: ListRunEvents(uow_factory, clock).after(parsed_run_id, 0, 1)
    )
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

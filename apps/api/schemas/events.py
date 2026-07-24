"""RunEvent/TaskEvent response models. Read-only — there is no request body,
these entities are only ever produced by other use cases' side effects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from apps.api.pagination import Page
from friday.domain.event import RunEvent, RunEventType
from friday.domain.task_event import TaskEvent, TaskEventType


class RunEventResponse(BaseModel):
    event_id: str
    run_id: str
    step_id: str | None
    type: RunEventType
    sequence: int
    occurred_at: datetime
    payload: Any

    @classmethod
    def from_domain(cls, event: RunEvent) -> RunEventResponse:
        return cls(
            event_id=str(event.id),
            run_id=str(event.run_id),
            step_id=str(event.step_id) if event.step_id is not None else None,
            type=event.type,
            sequence=event.sequence,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )


class RunEventPage(BaseModel):
    items: list[RunEventResponse]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: Page[RunEvent]) -> RunEventPage:
        return cls(
            items=[RunEventResponse.from_domain(e) for e in page.items],
            next_cursor=page.next_cursor,
        )


class TaskEventResponse(BaseModel):
    event_id: str
    task_id: str
    type: TaskEventType
    sequence: int
    occurred_at: datetime
    payload: Any

    @classmethod
    def from_domain(cls, event: TaskEvent) -> TaskEventResponse:
        return cls(
            event_id=str(event.id),
            task_id=str(event.task_id),
            type=event.type,
            sequence=event.sequence,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )


class TaskEventPage(BaseModel):
    items: list[TaskEventResponse]
    next_cursor: str | None

    @classmethod
    def from_page(cls, page: Page[TaskEvent]) -> TaskEventPage:
        return cls(
            items=[TaskEventResponse.from_domain(e) for e in page.items],
            next_cursor=page.next_cursor,
        )

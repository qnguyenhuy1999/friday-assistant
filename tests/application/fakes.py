"""In-memory fakes for application use-case tests.

`FakeUnitOfWork` implements the full `UnitOfWork` protocol but only the
repositories the current use cases touch (tasks, runs, events) hold real
in-memory state; the rest raise if accessed, which would flag scope creep."""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Self

from friday.application.ports import (
    ApprovalRepository,
    ArtifactRepository,
    RunEventStore,
    RunRepository,
    RunStepRepository,
    TaskRepository,
    ToolInvocationRepository,
)
from friday.domain.event import RunEvent
from friday.domain.identifiers import RunId, TaskId
from friday.domain.run import Run
from friday.domain.task import Task

T0 = datetime(2026, 1, 2, 3, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.fixed_now = now

    def now(self) -> datetime:
        return self.fixed_now


class FakeTaskRepository:
    def __init__(self) -> None:
        self.items: dict[TaskId, Task] = {}
        self.saved: list[TaskId] = []

    def add(self, task: Task) -> None:
        self.items[task.id] = task

    def get(self, task_id: TaskId) -> Task | None:
        return self.items.get(task_id)

    def save(self, task: Task) -> None:
        self.items[task.id] = task
        self.saved.append(task.id)


class FakeRunRepository:
    def __init__(self) -> None:
        self.items: dict[RunId, Run] = {}

    def add(self, run: Run) -> None:
        self.items[run.id] = run

    def get(self, run_id: RunId) -> Run | None:
        return self.items.get(run_id)

    def save(self, run: Run) -> None:
        self.items[run.id] = run

    def list_for_task(self, task_id: TaskId) -> list[Run]:
        matching = [run for run in self.items.values() if run.task_id == task_id]
        return sorted(matching, key=lambda run: (run.created_at, str(run.id)))


class FakeRunEventStore:
    def __init__(self) -> None:
        self.appended: list[RunEvent] = []

    def append(self, event: RunEvent) -> None:
        self.appended.append(event)

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        matching = [event for event in self.appended if event.run_id == run_id]
        return sorted(matching, key=lambda event: event.sequence)

    def next_sequence(self, run_id: RunId) -> int:
        sequences = [event.sequence for event in self.appended if event.run_id == run_id]
        return max(sequences, default=0) + 1


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.task_repo = FakeTaskRepository()
        self.run_repo = FakeRunRepository()
        self.event_store = FakeRunEventStore()
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    @property
    def tasks(self) -> TaskRepository:
        return self.task_repo

    @property
    def runs(self) -> RunRepository:
        return self.run_repo

    @property
    def steps(self) -> RunStepRepository:
        raise NotImplementedError("steps are not part of Phase 6 use cases")

    @property
    def approvals(self) -> ApprovalRepository:
        raise NotImplementedError("approvals are not part of Phase 6 use cases")

    @property
    def artifacts(self) -> ArtifactRepository:
        raise NotImplementedError("artifacts are not part of Phase 6 use cases")

    @property
    def tool_invocations(self) -> ToolInvocationRepository:
        raise NotImplementedError("tool invocations are not part of Phase 6 use cases")

    @property
    def events(self) -> RunEventStore:
        return self.event_store

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        self.closed = True

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class CountingUnitOfWorkFactory:
    """Returns one prepared FakeUnitOfWork and counts invocations."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        self.uow = uow
        self.calls = 0

    def __call__(self) -> FakeUnitOfWork:
        self.calls += 1
        return self.uow

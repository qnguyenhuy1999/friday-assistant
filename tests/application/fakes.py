"""In-memory fakes for application use-case tests.

`FakeUnitOfWork` implements the full `UnitOfWork` protocol; every
repository holds real in-memory state as of Phase 8."""

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
    TaskEventStore,
    TaskRepository,
    ToolInvocationRepository,
)
from friday.domain.approval import ApprovalRequest, ApprovalStatus
from friday.domain.artifact import Artifact
from friday.domain.event import RunEvent
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from friday.domain.task_event import TaskEvent
from friday.domain.tool import ToolInvocation

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

    def list(self, limit: int) -> list[Task]:
        return sorted(self.items.values(), key=lambda task: (task.created_at, str(task.id)))[:limit]


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


class FakeTaskEventStore:
    def __init__(self) -> None:
        self.appended: list[TaskEvent] = []

    def append(self, event: TaskEvent) -> None:
        self.appended.append(event)

    def next_sequence(self, task_id: TaskId) -> int:
        return sum(event.task_id == task_id for event in self.appended) + 1

    def list_for_task(self, task_id: TaskId) -> list[TaskEvent]:
        return sorted(
            (event for event in self.appended if event.task_id == task_id),
            key=lambda event: event.sequence,
        )


class FakeRunStepRepository:
    def __init__(self) -> None:
        self.items: dict[RunStepId, RunStep] = {}

    def add(self, step: RunStep) -> None:
        self.items[step.id] = step

    def get(self, step_id: RunStepId) -> RunStep | None:
        return self.items.get(step_id)

    def save(self, step: RunStep) -> None:
        self.items[step.id] = step

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        return sorted(
            (s for s in self.items.values() if s.run_id == run_id),
            key=lambda s: (s.position, str(s.id)),
        )


class FakeToolInvocationRepository:
    def __init__(self) -> None:
        self.items: dict[ToolInvocationId, ToolInvocation] = {}

    def add(self, item: ToolInvocation) -> None:
        self.items[item.id] = item

    def get(self, item_id: ToolInvocationId) -> ToolInvocation | None:
        return self.items.get(item_id)

    def save(self, item: ToolInvocation) -> None:
        self.items[item.id] = item

    def list_for_run(self, run_id: RunId) -> list[ToolInvocation]:
        return sorted(
            (i for i in self.items.values() if i.run_id == run_id),
            key=lambda i: (i.requested_at, str(i.id)),
        )

    def list_for_step(self, step_id: RunStepId) -> list[ToolInvocation]:
        return sorted(
            (i for i in self.items.values() if i.step_id == step_id),
            key=lambda i: (i.requested_at, str(i.id)),
        )


class FakeApprovalRepository:
    def __init__(self) -> None:
        self.items: dict[ApprovalRequestId, ApprovalRequest] = {}

    def add(self, approval: ApprovalRequest) -> None:
        self.items[approval.id] = approval

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None:
        return self.items.get(approval_id)

    def save(self, approval: ApprovalRequest) -> None:
        self.items[approval.id] = approval

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        matching = [
            a
            for a in self.items.values()
            if a.run_id == run_id and a.status is ApprovalStatus.PENDING
        ]
        return sorted(matching, key=lambda a: (a.requested_at, str(a.id)))

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        return sorted(
            (approval for approval in self.items.values() if approval.run_id == run_id),
            key=lambda approval: (approval.requested_at, str(approval.id)),
        )


class FakeArtifactRepository:
    def __init__(self) -> None:
        self.items: dict[ArtifactId, Artifact] = {}

    def add(self, artifact: Artifact) -> None:
        self.items[artifact.id] = artifact

    def get(self, artifact_id: ArtifactId) -> Artifact | None:
        return self.items.get(artifact_id)

    def list_for_run(self, run_id: RunId) -> list[Artifact]:
        return sorted(
            (a for a in self.items.values() if a.run_id == run_id),
            key=lambda a: (a.created_at, str(a.id)),
        )


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.task_repo = FakeTaskRepository()
        self.run_repo = FakeRunRepository()
        self.event_store = FakeRunEventStore()
        self.task_event_store = FakeTaskEventStore()
        self.step_repo = FakeRunStepRepository()
        self.tool_repo = FakeToolInvocationRepository()
        self.approval_repo = FakeApprovalRepository()
        self.artifact_repo = FakeArtifactRepository()
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
        return self.step_repo

    @property
    def approvals(self) -> ApprovalRepository:
        return self.approval_repo

    @property
    def artifacts(self) -> ArtifactRepository:
        return self.artifact_repo

    @property
    def tool_invocations(self) -> ToolInvocationRepository:
        return self.tool_repo

    @property
    def events(self) -> RunEventStore:
        return self.event_store

    @property
    def task_events(self) -> TaskEventStore:
        return self.task_event_store

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

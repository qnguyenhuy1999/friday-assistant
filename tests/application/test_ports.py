"""Application ports: minimal in-memory fakes proving each Protocol's shape
is structurally satisfiable and its documented list-ordering contract is
exercisable. No persistence/infrastructure code — these fakes exist only to
pin the port contracts down for later infrastructure implementations."""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from datetime import UTC, datetime

from friday.application.ports import (
    ApprovalRepository,
    ArtifactRepository,
    Clock,
    RunEventStore,
    RunRepository,
    RunStepRepository,
    TaskRepository,
    ToolInvocationRepository,
)
from friday.domain.approval import ApprovalCategory, ApprovalRequest
from friday.domain.artifact import Artifact, ArtifactKind
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunEventId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.run import Run
from friday.domain.step import RunStep
from friday.domain.task import Task
from friday.domain.tool import ToolInvocation

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 1, tzinfo=UTC)


@dataclass
class _FakeClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass
class _FakeTaskRepository:
    _tasks: dict[TaskId, Task] = field(default_factory=dict)

    def add(self, task: Task) -> None:
        self._tasks[task.id] = task

    def get(self, task_id: TaskId) -> Task | None:
        return self._tasks.get(task_id)

    def save(self, task: Task) -> None:
        self._tasks[task.id] = task

    def list(self, limit: int) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda task: (task.created_at, task.id.value))[
            :limit
        ]

    def list_page(
        self, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> builtins.list[Task]:
        tasks = self.list(len(self._tasks))
        if after_created_at is not None and after_id is not None:
            tasks = [
                task
                for task in tasks
                if (task.created_at, task.id.value) > (after_created_at, after_id)
            ]
        return tasks[:limit]


@dataclass
class _FakeRunRepository:
    _runs: dict[RunId, Run] = field(default_factory=dict)

    def add(self, run: Run) -> None:
        self._runs[run.id] = run

    def get(self, run_id: RunId) -> Run | None:
        return self._runs.get(run_id)

    def save(self, run: Run) -> None:
        self._runs[run.id] = run

    def list_for_task(self, task_id: TaskId) -> list[Run]:
        matches = [run for run in self._runs.values() if run.task_id == task_id]
        return sorted(matches, key=lambda run: (run.created_at, run.id.value))

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Run]:
        runs = self.list_for_task(task_id)
        if after_created_at is not None and after_id is not None:
            runs = [
                run for run in runs if (run.created_at, run.id.value) > (after_created_at, after_id)
            ]
        return runs[:limit]


@dataclass
class _FakeRunStepRepository:
    _steps: dict[RunStepId, RunStep] = field(default_factory=dict)

    def add(self, step: RunStep) -> None:
        self._steps[step.id] = step

    def get(self, step_id: RunStepId) -> RunStep | None:
        return self._steps.get(step_id)

    def save(self, step: RunStep) -> None:
        self._steps[step.id] = step

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        matches = [step for step in self._steps.values() if step.run_id == run_id]
        return sorted(matches, key=lambda step: (step.position, step.id.value))

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_position: int | None, after_id: str | None
    ) -> list[RunStep]:
        steps = self.list_for_run(run_id)
        if after_position is not None and after_id is not None:
            steps = [
                step
                for step in steps
                if (step.position, step.id.value) > (after_position, after_id)
            ]
        return steps[:limit]


@dataclass
class _FakeApprovalRepository:
    _approvals: dict[ApprovalRequestId, ApprovalRequest] = field(default_factory=dict)

    def add(self, approval: ApprovalRequest) -> None:
        self._approvals[approval.id] = approval

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None:
        return self._approvals.get(approval_id)

    def save(self, approval: ApprovalRequest) -> None:
        self._approvals[approval.id] = approval

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        matches = [a for a in self._approvals.values() if a.run_id == run_id]
        return sorted(matches, key=lambda a: (a.requested_at, a.id.value))

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        matches = [a for a in self._approvals.values() if a.run_id == run_id]
        return sorted(matches, key=lambda a: (a.requested_at, a.id.value))

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ApprovalRequest]:
        approvals = self.list_for_run(run_id)
        if after_requested_at is not None and after_id is not None:
            approvals = [
                approval
                for approval in approvals
                if (approval.requested_at, approval.id.value) > (after_requested_at, after_id)
            ]
        return approvals[:limit]


@dataclass
class _FakeArtifactRepository:
    _artifacts: dict[ArtifactId, Artifact] = field(default_factory=dict)

    def add(self, artifact: Artifact) -> None:
        self._artifacts[artifact.id] = artifact

    def get(self, artifact_id: ArtifactId) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    def list_for_run(self, run_id: RunId) -> list[Artifact]:
        matches = [a for a in self._artifacts.values() if a.run_id == run_id]
        return sorted(matches, key=lambda a: (a.created_at, a.id.value))

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Artifact]:
        artifacts = self.list_for_run(run_id)
        if after_created_at is not None and after_id is not None:
            artifacts = [
                artifact
                for artifact in artifacts
                if (artifact.created_at, artifact.id.value) > (after_created_at, after_id)
            ]
        return artifacts[:limit]


@dataclass
class _FakeToolInvocationRepository:
    _invocations: dict[ToolInvocationId, ToolInvocation] = field(default_factory=dict)

    def add(self, invocation: ToolInvocation) -> None:
        self._invocations[invocation.id] = invocation

    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None:
        return self._invocations.get(invocation_id)

    def save(self, invocation: ToolInvocation) -> None:
        self._invocations[invocation.id] = invocation

    def list_for_run(self, run_id: RunId) -> list[ToolInvocation]:
        matches = [i for i in self._invocations.values() if i.run_id == run_id]
        return sorted(matches, key=lambda i: (i.requested_at, i.id.value))

    def list_for_step(self, step_id: RunStepId) -> list[ToolInvocation]:
        matches = [i for i in self._invocations.values() if i.step_id == step_id]
        return sorted(matches, key=lambda i: (i.requested_at, i.id.value))

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ToolInvocation]:
        invocations = self.list_for_run(run_id)
        if after_requested_at is not None and after_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if (invocation.requested_at, invocation.id.value) > (after_requested_at, after_id)
            ]
        return invocations[:limit]

    def list_for_step_page(
        self,
        step_id: RunStepId,
        limit: int,
        after_requested_at: datetime | None,
        after_id: str | None,
    ) -> list[ToolInvocation]:
        invocations = self.list_for_step(step_id)
        if after_requested_at is not None and after_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if (invocation.requested_at, invocation.id.value) > (after_requested_at, after_id)
            ]
        return invocations[:limit]


@dataclass
class _FakeRunEventStore:
    _events: dict[RunId, list[RunEvent]] = field(default_factory=dict)

    def append(self, event: RunEvent) -> None:
        self._events.setdefault(event.run_id, []).append(event)

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        return sorted(self._events.get(run_id, []), key=lambda e: e.sequence)

    def list_after_sequence(self, run_id: RunId, after_sequence: int, limit: int) -> list[RunEvent]:
        return [event for event in self.list_for_run(run_id) if event.sequence > after_sequence][
            :limit
        ]

    def next_sequence(self, run_id: RunId) -> int:
        return len(self._events.get(run_id, [])) + 1


def test_fake_clock_satisfies_clock_protocol() -> None:
    clock: Clock = _FakeClock(current=T0)
    assert clock.now() == T0


def test_task_repository_add_get_save_round_trip() -> None:
    repo: TaskRepository = _FakeTaskRepository()
    task = Task.new(id=TaskId.new(), title="t", description="d", created_at=T0)
    repo.add(task)
    assert repo.get(task.id) is task
    task.start(T1)
    repo.save(task)
    assert repo.get(task.id) is task


def test_task_repository_get_missing_returns_none() -> None:
    repo: TaskRepository = _FakeTaskRepository()
    assert repo.get(TaskId.new()) is None


def test_run_repository_lists_ordered_by_created_at_then_id() -> None:
    repo: RunRepository = _FakeRunRepository()
    task_id = TaskId.new()
    other_task_id = TaskId.new()
    later = Run.new(id=RunId.new(), task_id=task_id, created_at=T1)
    earlier = Run.new(id=RunId.new(), task_id=task_id, created_at=T0)
    unrelated = Run.new(id=RunId.new(), task_id=other_task_id, created_at=T0)
    for run in (later, earlier, unrelated):
        repo.add(run)
    assert repo.list_for_task(task_id) == [earlier, later]


def test_run_step_repository_lists_ordered_by_position() -> None:
    repo: RunStepRepository = _FakeRunStepRepository()
    run_id = RunId.new()
    second = RunStep.new(id=RunStepId.new(), run_id=run_id, name="b", position=1, created_at=T0)
    first = RunStep.new(id=RunStepId.new(), run_id=run_id, name="a", position=0, created_at=T0)
    for step in (second, first):
        repo.add(step)
    assert repo.list_for_run(run_id) == [first, second]


def test_approval_repository_lists_ordered_by_requested_at() -> None:
    repo: ApprovalRepository = _FakeApprovalRepository()
    run_id = RunId.new()
    later = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.OTHER,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=T1,
    )
    earlier = ApprovalRequest.new(
        id=ApprovalRequestId.new(),
        run_id=run_id,
        category=ApprovalCategory.OTHER,
        summary="s",
        reason="r",
        requested_action="a",
        requested_input=None,
        requested_at=T0,
    )
    for approval in (later, earlier):
        repo.add(approval)
    assert repo.list_pending_for_run(run_id) == [earlier, later]


def test_artifact_repository_lists_ordered_by_created_at() -> None:
    repo: ArtifactRepository = _FakeArtifactRepository()
    run_id = RunId.new()
    later = Artifact(
        id=ArtifactId.new(),
        run_id=run_id,
        kind=ArtifactKind.TEXT,
        name="b",
        media_type="text/plain",
        location="loc-b",
        created_at=T1,
    )
    earlier = Artifact(
        id=ArtifactId.new(),
        run_id=run_id,
        kind=ArtifactKind.TEXT,
        name="a",
        media_type="text/plain",
        location="loc-a",
        created_at=T0,
    )
    for artifact in (later, earlier):
        repo.add(artifact)
    assert repo.list_for_run(run_id) == [earlier, later]


def test_tool_invocation_repository_lists_ordered_by_requested_at() -> None:
    repo: ToolInvocationRepository = _FakeToolInvocationRepository()
    run_id = RunId.new()
    later = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="t",
        requested_input=None,
        requested_at=T1,
    )
    earlier = ToolInvocation.new(
        id=ToolInvocationId.new(),
        run_id=run_id,
        tool_name="t",
        requested_input=None,
        requested_at=T0,
    )
    for invocation in (later, earlier):
        repo.add(invocation)
    assert repo.list_for_run(run_id) == [earlier, later]


def test_run_event_store_next_sequence_and_ordering() -> None:
    store: RunEventStore = _FakeRunEventStore()
    run_id = RunId.new()
    assert store.next_sequence(run_id) == 1
    first = RunEvent(
        id=RunEventId.new(),
        run_id=run_id,
        type=RunEventType.RUN_CREATED,
        sequence=1,
        occurred_at=T0,
    )
    store.append(first)
    assert store.next_sequence(run_id) == 2
    second = RunEvent(
        id=RunEventId.new(),
        run_id=run_id,
        type=RunEventType.RUN_STARTED,
        sequence=2,
        occurred_at=T1,
    )
    store.append(second)
    assert store.list_for_run(run_id) == [first, second]


def test_run_event_store_next_sequence_is_per_run() -> None:
    store: RunEventStore = _FakeRunEventStore()
    run_id = RunId.new()
    other_run_id = RunId.new()
    store.append(
        RunEvent(
            id=RunEventId.new(),
            run_id=run_id,
            type=RunEventType.RUN_CREATED,
            sequence=1,
            occurred_at=T0,
        )
    )
    assert store.next_sequence(other_run_id) == 1

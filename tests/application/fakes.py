"""In-memory fakes for application use-case tests.

`FakeUnitOfWork` implements the full `UnitOfWork` protocol; every
repository holds real in-memory state as of Phase 8."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

from friday.application.ports import (
    ApprovalRepository,
    ArtifactRepository,
    RunEventStore,
    RunRepository,
    RunStepRepository,
    RunWorkItemView,
    RunWorkQueue,
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

    def list_page(
        self, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> builtins.list[Task]:
        tasks = self.list(len(self.items))
        if after_created_at is not None and after_id is not None:
            tasks = [
                task
                for task in tasks
                if (task.created_at, str(task.id)) > (after_created_at, after_id)
            ]
        return tasks[:limit]


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

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Run]:
        runs = self.list_for_task(task_id)
        if after_created_at is not None and after_id is not None:
            runs = [
                run for run in runs if (run.created_at, str(run.id)) > (after_created_at, after_id)
            ]
        return runs[:limit]


class FakeRunEventStore:
    def __init__(self) -> None:
        self.appended: list[RunEvent] = []
        self._next_sequences: dict[RunId, int] = {}

    def append(self, event: RunEvent) -> None:
        self.appended.append(event)

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        matching = [event for event in self.appended if event.run_id == run_id]
        return sorted(matching, key=lambda event: event.sequence)

    def list_after_sequence(self, run_id: RunId, after_sequence: int, limit: int) -> list[RunEvent]:
        return [event for event in self.list_for_run(run_id) if event.sequence > after_sequence][
            :limit
        ]

    def reserve_sequences(self, run_id: RunId, count: int) -> int:
        start = self._next_sequences.get(run_id, 1)
        self._next_sequences[run_id] = start + count
        return start


class FakeTaskEventStore:
    def __init__(self) -> None:
        self.appended: list[TaskEvent] = []
        self._next_sequences: dict[TaskId, int] = {}

    def append(self, event: TaskEvent) -> None:
        self.appended.append(event)

    def reserve_sequences(self, task_id: TaskId, count: int) -> int:
        start = self._next_sequences.get(task_id, 1)
        self._next_sequences[task_id] = start + count
        return start

    def list_for_task(self, task_id: TaskId) -> list[TaskEvent]:
        return sorted(
            (event for event in self.appended if event.task_id == task_id),
            key=lambda event: event.sequence,
        )

    def list_after_sequence(
        self, task_id: TaskId, after_sequence: int, limit: int
    ) -> list[TaskEvent]:
        return [event for event in self.list_for_task(task_id) if event.sequence > after_sequence][
            :limit
        ]


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

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_position: int | None, after_id: str | None
    ) -> list[RunStep]:
        steps = self.list_for_run(run_id)
        if after_position is not None and after_id is not None:
            steps = [
                step for step in steps if (step.position, str(step.id)) > (after_position, after_id)
            ]
        return steps[:limit]


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

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ToolInvocation]:
        invocations = self.list_for_run(run_id)
        if after_requested_at is not None and after_id is not None:
            invocations = [
                invocation
                for invocation in invocations
                if (invocation.requested_at, str(invocation.id)) > (after_requested_at, after_id)
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
                if (invocation.requested_at, str(invocation.id)) > (after_requested_at, after_id)
            ]
        return invocations[:limit]


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

    def list_due_for_expiry(self, now: datetime, limit: int) -> list[ApprovalRequest]:
        matching = [
            approval
            for approval in self.items.values()
            if approval.status is ApprovalStatus.PENDING
            and approval.expires_at is not None
            and approval.expires_at <= now
        ]
        matching.sort(key=lambda approval: (approval.requested_at, str(approval.id)))
        return matching[:limit]

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        return sorted(
            (approval for approval in self.items.values() if approval.run_id == run_id),
            key=lambda approval: (approval.requested_at, str(approval.id)),
        )

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ApprovalRequest]:
        approvals = self.list_for_run(run_id)
        if after_requested_at is not None and after_id is not None:
            approvals = [
                approval
                for approval in approvals
                if (approval.requested_at, str(approval.id)) > (after_requested_at, after_id)
            ]
        return approvals[:limit]


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

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Artifact]:
        artifacts = self.list_for_run(run_id)
        if after_created_at is not None and after_id is not None:
            artifacts = [
                artifact
                for artifact in artifacts
                if (artifact.created_at, str(artifact.id)) > (after_created_at, after_id)
            ]
        return artifacts[:limit]


class FakeRunWorkQueue:
    def __init__(self) -> None:
        self.items: dict[RunId, RunWorkItemView] = {}

    def enqueue(self, run_id: RunId, available_at: datetime, enqueued_at: datetime) -> None:
        existing = self.items.get(run_id)
        if existing is None:
            self.items[run_id] = RunWorkItemView(
                run_id=run_id,
                available_at=available_at,
                enqueued_at=enqueued_at,
                claimed_by=None,
                claim_token=None,
                claim_generation=0,
                claimed_at=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
            return
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=available_at,
            enqueued_at=enqueued_at,
            claimed_by=existing.claimed_by,
            claim_token=existing.claim_token,
            claim_generation=existing.claim_generation,
            claimed_at=existing.claimed_at,
            heartbeat_at=existing.heartbeat_at,
            lease_expires_at=existing.lease_expires_at,
        )

    def get(self, run_id: RunId) -> RunWorkItemView | None:
        return self.items.get(run_id)

    def find_due_candidates(self, now: datetime, limit: int) -> builtins.list[RunWorkItemView]:
        candidates = [
            item
            for item in self.items.values()
            if item.available_at <= now
            and (
                item.claimed_by is None
                or (item.lease_expires_at is not None and item.lease_expires_at <= now)
            )
        ]
        candidates.sort(key=lambda item: (item.available_at, item.enqueued_at, str(item.run_id)))
        return candidates[:limit]

    def find_expired_claims(self, now: datetime, limit: int) -> builtins.list[RunWorkItemView]:
        candidates = [
            item
            for item in self.items.values()
            if item.claimed_by is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at <= now
        ]
        candidates.sort(key=lambda item: (item.available_at, item.enqueued_at, str(item.run_id)))
        return candidates[:limit]

    def remove(self, run_id: RunId) -> None:
        self.items.pop(run_id, None)

    def try_claim(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        if item is None or item.available_at > now:
            return False
        if item.claimed_by is not None and not (
            item.lease_expires_at is not None and item.lease_expires_at <= now
        ):
            return False
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=worker_id,
            claim_token=claim_token,
            claim_generation=item.claim_generation + 1,
            claimed_at=now,
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        return True

    def renew_lease(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns(item, worker_id, claim_token, claim_generation):
            return False
        assert item is not None
        if item.lease_expires_at is None or item.lease_expires_at <= now:
            return False
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=item.claimed_by,
            claim_token=item.claim_token,
            claim_generation=item.claim_generation,
            claimed_at=item.claimed_at,
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        return True

    def release_claim(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns_active(item, worker_id, claim_token, claim_generation, now):
            return False
        assert item is not None
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=None,
            claim_token=None,
            claim_generation=item.claim_generation,
            claimed_at=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return True

    def requeue_claimed(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        available_at: datetime,
        enqueued_at: datetime,
        now: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns_active(item, worker_id, claim_token, claim_generation, now):
            return False
        assert item is not None
        self.items[run_id] = RunWorkItemView(
            run_id=run_id,
            available_at=available_at,
            enqueued_at=enqueued_at,
            claimed_by=None,
            claim_token=None,
            claim_generation=item.claim_generation,
            claimed_at=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return True

    def remove_if_claimed(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        item = self.items.get(run_id)
        if not self._owns_active(item, worker_id, claim_token, claim_generation, now):
            return False
        del self.items[run_id]
        return True

    def clear_expired_claim(self, run_id: RunId, now: datetime) -> bool:
        item = self.items.get(run_id)
        if not self._is_expired(item, now):
            return False
        assert item is not None
        self.items[run_id] = RunWorkItemView(
            run_id=item.run_id,
            available_at=item.available_at,
            enqueued_at=item.enqueued_at,
            claimed_by=None,
            claim_token=None,
            claim_generation=item.claim_generation,
            claimed_at=None,
            heartbeat_at=None,
            lease_expires_at=None,
        )
        return True

    def remove_if_lease_expired(self, run_id: RunId, now: datetime) -> bool:
        item = self.items.get(run_id)
        if not self._is_expired(item, now):
            return False
        del self.items[run_id]
        return True

    def is_claim_active(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        item = self.items.get(run_id)
        return (
            self._owns(item, worker_id, claim_token, claim_generation)
            and item is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at > now
        )

    @staticmethod
    def _owns(
        item: RunWorkItemView | None, worker_id: str, claim_token: str, claim_generation: int
    ) -> bool:
        return (
            item is not None
            and item.claimed_by == worker_id
            and item.claim_token == claim_token
            and item.claim_generation == claim_generation
        )

    @staticmethod
    def _owns_active(
        item: RunWorkItemView | None,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        return (
            FakeRunWorkQueue._owns(item, worker_id, claim_token, claim_generation)
            and item is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at > now
        )

    @staticmethod
    def _is_expired(item: RunWorkItemView | None, now: datetime) -> bool:
        return (
            item is not None
            and item.claimed_by is not None
            and item.lease_expires_at is not None
            and item.lease_expires_at <= now
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
        self.work_queue_repo = FakeRunWorkQueue()
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

    @property
    def work_queue(self) -> RunWorkQueue:
        return self.work_queue_repo

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

"""Application ports: framework-independent protocols for persistence, the
event store, and the clock. No implementation lives here — that's for a
later infrastructure phase.

Missing-record convention: `get(...)` returns `Entity | None`. Not-found
mapping (e.g. to an HTTP 404) is an application/infrastructure concern, not
a port concern.

List ordering is part of each port's contract, documented per method below.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self

from friday.domain.approval import ApprovalRequest
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


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class RunWorkItemView:
    run_id: RunId
    available_at: datetime
    enqueued_at: datetime
    claimed_by: str | None
    claim_token: str | None
    claim_generation: int
    claimed_at: datetime | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None


class RunWorkQueue(Protocol):
    def enqueue(self, run_id: RunId, available_at: datetime, enqueued_at: datetime) -> None: ...
    def get(self, run_id: RunId) -> RunWorkItemView | None: ...
    def find_due_candidates(self, now: datetime, limit: int) -> list[RunWorkItemView]: ...
    def find_expired_claims(self, now: datetime, limit: int) -> list[RunWorkItemView]: ...
    def remove(self, run_id: RunId) -> None: ...

    def try_claim(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Atomically claim a due-or-expired work item in one conditional
        UPDATE. Returns False (never raises) when the row no longer matches
        — i.e. another worker won the race — so callers treat a lost claim
        as an ordinary outcome, not an error."""
        ...

    def renew_lease(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Conditional on an exact (worker_id, claim_token, claim_generation)
        match and an unexpired lease. Returns False on any mismatch."""
        ...

    def release_claim(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> bool:
        """Clear ownership/lease fields but keep the row (and its
        claim_generation) claimable again. Returns False on ownership
        mismatch."""
        ...

    def requeue_claimed(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        available_at: datetime,
        enqueued_at: datetime,
    ) -> bool:
        """Release ownership and reschedule availability in one conditional
        UPDATE. Returns False on ownership mismatch."""
        ...

    def remove_if_claimed(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int
    ) -> bool:
        """Delete the work item only if still owned by this exact claim.
        Returns False on ownership mismatch."""
        ...

    def clear_expired_claim(self, run_id: RunId, now: datetime) -> bool: ...

    def remove_if_lease_expired(self, run_id: RunId, now: datetime) -> bool: ...

    def is_claim_active(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        """Read-only check: does this exact claim still hold an unexpired
        lease?"""
        ...


class TaskRepository(Protocol):
    def add(self, task: Task) -> None: ...
    def get(self, task_id: TaskId) -> Task | None: ...
    def save(self, task: Task) -> None: ...
    def list(self, limit: int) -> list[Task]: ...
    def list_page(
        self, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> builtins.list[Task]: ...


class RunRepository(Protocol):
    def add(self, run: Run) -> None: ...
    def get(self, run_id: RunId) -> Run | None: ...
    def save(self, run: Run) -> None: ...

    def list_for_task(self, task_id: TaskId) -> list[Run]:
        """Ordered by created_at, then id."""
        ...

    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Run]: ...


class RunStepRepository(Protocol):
    def add(self, step: RunStep) -> None: ...
    def get(self, step_id: RunStepId) -> RunStep | None: ...
    def save(self, step: RunStep) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        """Ordered by position, then id."""
        ...

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_position: int | None, after_id: str | None
    ) -> list[RunStep]: ...


class ApprovalRepository(Protocol):
    def add(self, approval: ApprovalRequest) -> None: ...
    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None: ...
    def save(self, approval: ApprovalRequest) -> None: ...

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        """Ordered by requested_at, then id."""
        ...

    def list_due_for_expiry(self, now: datetime, limit: int) -> list[ApprovalRequest]: ...

    def list_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        """Ordered by requested_at, then id."""
        ...

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ApprovalRequest]: ...


class ArtifactRepository(Protocol):
    def add(self, artifact: Artifact) -> None: ...
    def get(self, artifact_id: ArtifactId) -> Artifact | None: ...

    def list_for_run(self, run_id: RunId) -> list[Artifact]:
        """Ordered by created_at, then id."""
        ...

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Artifact]: ...


class ToolInvocationRepository(Protocol):
    def add(self, invocation: ToolInvocation) -> None: ...
    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None: ...
    def save(self, invocation: ToolInvocation) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[ToolInvocation]:
        """Ordered by requested_at, then id."""
        ...

    def list_for_step(self, step_id: RunStepId) -> list[ToolInvocation]: ...
    def list_for_run_page(
        self, run_id: RunId, limit: int, after_requested_at: datetime | None, after_id: str | None
    ) -> list[ToolInvocation]: ...
    def list_for_step_page(
        self,
        step_id: RunStepId,
        limit: int,
        after_requested_at: datetime | None,
        after_id: str | None,
    ) -> list[ToolInvocation]: ...


class RunEventStore(Protocol):
    def append(self, event: RunEvent) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        """Ordered by sequence."""
        ...

    def list_after_sequence(
        self, run_id: RunId, after_sequence: int, limit: int
    ) -> list[RunEvent]: ...

    def reserve_sequences(self, run_id: RunId, count: int) -> int:
        """Atomically reserve a sequence block; count must be >= 1."""
        ...


class TaskEventStore(Protocol):
    def append(self, event: TaskEvent) -> None: ...

    def reserve_sequences(self, task_id: TaskId, count: int) -> int:
        """Atomically reserve a sequence block; count must be >= 1."""
        ...

    def list_for_task(self, task_id: TaskId) -> list[TaskEvent]:
        """Ordered by sequence."""
        ...

    def list_after_sequence(
        self, task_id: TaskId, after_sequence: int, limit: int
    ) -> list[TaskEvent]: ...


class UnitOfWork(Protocol):
    """One shared transaction boundary across the repositories/event store a
    use case needs. A use case opens exactly one UnitOfWork, does its work
    through the exposed repositories, then calls `commit()` once; any
    exception before that should leave nothing durable (`rollback()`, called
    explicitly or via `__exit__`, undoes all staged writes)."""

    @property
    def tasks(self) -> TaskRepository: ...
    @property
    def runs(self) -> RunRepository: ...
    @property
    def steps(self) -> RunStepRepository: ...
    @property
    def approvals(self) -> ApprovalRepository: ...
    @property
    def artifacts(self) -> ArtifactRepository: ...
    @property
    def tool_invocations(self) -> ToolInvocationRepository: ...
    @property
    def events(self) -> RunEventStore: ...
    @property
    def task_events(self) -> TaskEventStore: ...
    @property
    def work_queue(self) -> RunWorkQueue: ...

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]

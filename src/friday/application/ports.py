"""Application ports: framework-independent protocols for persistence, the
event store, and the clock. No implementation lives here — that's for a
later infrastructure phase.

Missing-record convention: `get(...)` returns `Entity | None`. Not-found
mapping (e.g. to an HTTP 404) is an application/infrastructure concern, not
a port concern.

List ordering is part of each port's contract, documented per method below.
"""

from __future__ import annotations

from collections.abc import Callable
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
from friday.domain.tool import ToolInvocation


class Clock(Protocol):
    def now(self) -> datetime: ...


class TaskRepository(Protocol):
    def add(self, task: Task) -> None: ...
    def get(self, task_id: TaskId) -> Task | None: ...
    def save(self, task: Task) -> None: ...


class RunRepository(Protocol):
    def add(self, run: Run) -> None: ...
    def get(self, run_id: RunId) -> Run | None: ...
    def save(self, run: Run) -> None: ...

    def list_for_task(self, task_id: TaskId) -> list[Run]:
        """Ordered by created_at, then id."""
        ...


class RunStepRepository(Protocol):
    def add(self, step: RunStep) -> None: ...
    def get(self, step_id: RunStepId) -> RunStep | None: ...
    def save(self, step: RunStep) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        """Ordered by position, then id."""
        ...


class ApprovalRepository(Protocol):
    def add(self, approval: ApprovalRequest) -> None: ...
    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None: ...
    def save(self, approval: ApprovalRequest) -> None: ...

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        """Ordered by requested_at, then id."""
        ...


class ArtifactRepository(Protocol):
    def add(self, artifact: Artifact) -> None: ...
    def get(self, artifact_id: ArtifactId) -> Artifact | None: ...

    def list_for_run(self, run_id: RunId) -> list[Artifact]:
        """Ordered by created_at, then id."""
        ...


class ToolInvocationRepository(Protocol):
    def add(self, invocation: ToolInvocation) -> None: ...
    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None: ...
    def save(self, invocation: ToolInvocation) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[ToolInvocation]:
        """Ordered by requested_at, then id."""
        ...


class RunEventStore(Protocol):
    def append(self, event: RunEvent) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        """Ordered by sequence."""
        ...

    def next_sequence(self, run_id: RunId) -> int:
        """The sequence number the next appended event for this run must use."""
        ...


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

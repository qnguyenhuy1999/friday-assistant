"""Immutable use-case output results. Expose typed domain identifiers only —
never ORM row instances or raw dicts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from friday.domain.failure import Failure
from friday.domain.identifiers import RunId, RunStepId, TaskId
from friday.domain.run import RunStatus
from friday.domain.step import RunStepStatus
from friday.domain.task import TaskStatus


@dataclass(frozen=True, slots=True)
class CreateTaskResult:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class StartRunResult:
    task_id: TaskId
    run_id: RunId


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: TaskId
    title: str
    description: str
    status: TaskStatus
    created_at: datetime
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: RunId
    task_id: TaskId
    status: RunStatus
    created_at: datetime
    failure: Failure | None


@dataclass(frozen=True, slots=True)
class RunStepResult:
    step_id: RunStepId
    run_id: RunId
    name: str
    position: int
    status: RunStepStatus
    failure: Failure | None

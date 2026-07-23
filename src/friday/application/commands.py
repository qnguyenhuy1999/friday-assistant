"""Immutable use-case input commands. Stdlib dataclasses only — no
Pydantic/FastAPI/ORM/dict/vendor types, no generic command bus."""

from __future__ import annotations

from dataclasses import dataclass

from friday.domain.failure import Failure
from friday.domain.identifiers import RunId, RunStepId, TaskId


@dataclass(frozen=True, slots=True)
class CreateTaskCommand:
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class StartRunCommand:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class CancelTaskCommand:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class CompleteTaskCommand:
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class FailTaskCommand:
    task_id: TaskId
    failure: Failure


@dataclass(frozen=True, slots=True)
class StartQueuedRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class CompleteRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class FailRunCommand:
    run_id: RunId
    failure: Failure


@dataclass(frozen=True, slots=True)
class CancelRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class RetryFailedRunCommand:
    run_id: RunId


@dataclass(frozen=True, slots=True)
class CreateOrderedStepCommand:
    run_id: RunId
    name: str


@dataclass(frozen=True, slots=True)
class StartStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class CompleteStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class FailStepCommand:
    step_id: RunStepId
    failure: Failure


@dataclass(frozen=True, slots=True)
class SkipPendingStepCommand:
    step_id: RunStepId


@dataclass(frozen=True, slots=True)
class CancelStepCommand:
    step_id: RunStepId

"""Application error hierarchy. Stable, framework-free: no HTTP status codes,
no Pydantic, no SQLAlchemy exception types, no raw DB error messages.
Infrastructure translates persistence failures into these at the boundary
(see infrastructure/persistence/unit_of_work.py); application and use-case
code raises and catches only these.
"""

from __future__ import annotations

from friday.domain.identifiers import RunId, RunStepId, TaskId


class ApplicationError(Exception):
    """Base class for all application-layer errors."""


class TaskNotFound(ApplicationError):
    def __init__(self, task_id: TaskId) -> None:
        self.task_id = task_id
        super().__init__(f"Task not found: {task_id}")


class RunNotFound(ApplicationError):
    def __init__(self, run_id: RunId) -> None:
        self.run_id = run_id
        super().__init__(f"Run not found: {run_id}")


class RunStepNotFound(ApplicationError):
    def __init__(self, step_id: RunStepId) -> None:
        self.step_id = step_id
        super().__init__(f"Run step not found: {step_id}")


class EntityConflict(ApplicationError):
    """A write violated an expected uniqueness or state constraint."""


class ConcurrencyConflict(ApplicationError):
    """A write lost an optimistic-concurrency or stale-data race."""


class TransactionFailure(ApplicationError):
    """A commit or rollback itself failed."""

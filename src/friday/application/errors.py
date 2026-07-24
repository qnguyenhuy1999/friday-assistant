"""Application error hierarchy. Stable, framework-free: no HTTP status codes,
no Pydantic, no SQLAlchemy exception types, no raw DB error messages.
Infrastructure translates persistence failures into these at the boundary
(see infrastructure/persistence/unit_of_work.py); application and use-case
code raises and catches only these.
"""

from __future__ import annotations

from friday.domain.identifiers import (
    ApprovalRequestId,
    ArtifactId,
    RunId,
    RunStepId,
    TaskId,
    ToolInvocationId,
)


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


class ApprovalNotFound(ApplicationError):
    def __init__(self, approval_id: ApprovalRequestId) -> None:
        self.approval_id = approval_id
        super().__init__(f"Approval request not found: {approval_id}")


class ToolInvocationNotFound(ApplicationError):
    def __init__(self, invocation_id: ToolInvocationId) -> None:
        self.invocation_id = invocation_id
        super().__init__(f"Tool invocation not found: {invocation_id}")


class ArtifactNotFound(ApplicationError):
    def __init__(self, artifact_id: ArtifactId) -> None:
        self.artifact_id = artifact_id
        super().__init__(f"Artifact not found: {artifact_id}")


class EntityConflict(ApplicationError):
    """A write violated an expected uniqueness or state constraint."""


class ConcurrencyConflict(ApplicationError):
    """A write lost an optimistic-concurrency or stale-data race."""


class TransactionFailure(ApplicationError):
    """A commit or rollback itself failed."""


class ClaimLost(ApplicationError):
    """A worker's claim no longer matches (expired, released, or fenced by
    a newer claim generation). The caller must stop treating the run as
    owned; it must not retry with the same token."""


class BrainUnavailable(ApplicationError):
    """The brain runtime could not be reached."""


class BrainTimeout(ApplicationError):
    """The brain runtime did not respond within the allotted time."""


class BrainProtocolError(ApplicationError):
    """The brain runtime's response violated the transport-level protocol."""


class BrainResponseInvalid(ApplicationError):
    """The brain proposed an action that does not match the brain-action
    contract (see friday.application.runtime_actions)."""


class ToolInputInvalid(ApplicationError):
    """A tool's input failed validation against its declared contract."""


class ToolNotFound(ApplicationError):
    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"Tool not found: {tool}")


class ToolExecutionDenied(ApplicationError):
    """A tool invocation was denied by policy."""


class ToolApprovalRequired(ApplicationError):
    """A tool invocation requires human approval before it can execute."""


class ToolExecutionAmbiguous(ApplicationError):
    """A tool invocation's outcome could not be determined."""


class WorkspaceAccessDenied(ApplicationError):
    """An operation attempted to access the workspace outside its granted
    boundary."""


class RuntimeBudgetExceeded(ApplicationError):
    """A run exceeded its allotted runtime budget (steps, tokens, or wall
    clock)."""

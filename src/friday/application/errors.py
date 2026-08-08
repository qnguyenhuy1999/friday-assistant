"""Application error hierarchy. Stable, framework-free: no HTTP status codes,
no Pydantic, no SQLAlchemy exception types, no raw DB error messages.
Infrastructure translates persistence failures into these at the boundary
(see infrastructure/persistence/unit_of_work.py); application and use-case
code raises and catches only these.
"""

from __future__ import annotations

from friday.domain.identifiers import (
    AgentId,
    AgentRevisionId,
    ApprovalRequestId,
    ArtifactId,
    ConversationId,
    ConversationTurnId,
    DelegationRequestId,
    RunId,
    RunStepId,
    ScheduleId,
    SkillId,
    SkillRevisionId,
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


class ScheduleNotFound(ApplicationError):
    def __init__(self, schedule_id: ScheduleId) -> None:
        self.schedule_id = schedule_id
        super().__init__(f"Schedule not found: {schedule_id}")


class ConversationNotFound(ApplicationError):
    def __init__(self, conversation_id: ConversationId) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Conversation not found: {conversation_id}")


class ConversationTurnNotFound(ApplicationError):
    def __init__(self, turn_id: ConversationTurnId) -> None:
        self.turn_id = turn_id
        super().__init__(f"Conversation turn not found: {turn_id}")


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


class SkillNotFound(ApplicationError):
    def __init__(self, skill_id: SkillId) -> None:
        self.skill_id = skill_id
        super().__init__(f"Skill not found: {skill_id}")


class SkillRevisionNotFound(ApplicationError):
    def __init__(self, revision_id: SkillRevisionId) -> None:
        self.revision_id = revision_id
        super().__init__(f"Skill revision not found: {revision_id}")


class SkillEvidenceSnapshotNotFound(ApplicationError):
    def __init__(self, snapshot_id: object) -> None:
        self.snapshot_id = snapshot_id
        super().__init__(f"Skill evidence snapshot not found: {snapshot_id}")


class SkillEvaluationSuiteNotFound(ApplicationError):
    def __init__(self, suite_id: object) -> None:
        self.suite_id = suite_id
        super().__init__(f"Skill evaluation suite not found: {suite_id}")


class SkillEvaluationRunNotFound(ApplicationError):
    def __init__(self, run_id: object) -> None:
        self.run_id = run_id
        super().__init__(f"Skill evaluation run not found: {run_id}")


class SkillImprovementProposalNotFound(ApplicationError):
    def __init__(self, proposal_id: object) -> None:
        self.proposal_id = proposal_id
        super().__init__(f"Skill improvement proposal not found: {proposal_id}")


class SkillPromotionRequestNotFound(ApplicationError):
    def __init__(self, request_id: object) -> None:
        self.request_id = request_id
        super().__init__(f"Skill promotion request not found: {request_id}")


class SkillRollbackRequestNotFound(ApplicationError):
    def __init__(self, request_id: object) -> None:
        self.request_id = request_id
        super().__init__(f"Skill rollback request not found: {request_id}")


class AgentNotFound(ApplicationError):
    def __init__(self, agent_id: AgentId) -> None:
        self.agent_id = agent_id
        super().__init__(f"Agent not found: {agent_id}")


class AgentRevisionNotFound(ApplicationError):
    def __init__(self, revision_id: AgentRevisionId) -> None:
        self.revision_id = revision_id
        super().__init__(f"Agent revision not found: {revision_id}")


class AgentIntegrityFailed(ApplicationError):
    """Persisted Agent revision content no longer matches its durable digest."""

    def __init__(self) -> None:
        super().__init__("agent_integrity_failed")


class DelegationRequestNotFound(ApplicationError):
    def __init__(self, delegation_id: DelegationRequestId) -> None:
        self.delegation_id = delegation_id
        super().__init__(f"Delegation request not found: {delegation_id}")


class UnknownBrainRuntimeKind(ApplicationError):
    """An AgentRevision named a runtime_kind with no registered code-owned
    adapter factory. Fails closed: unknown kinds never fall back to a
    default runtime."""

    def __init__(self, runtime_kind: str) -> None:
        self.runtime_kind = runtime_kind
        super().__init__(f"Unknown brain runtime kind: {runtime_kind}")


class SkillIntegrityFailed(ApplicationError):
    """Persisted Skill instructions no longer match their durable digest."""

    def __init__(self) -> None:
        super().__init__("skill_integrity_failed")


class SkillResolutionFailed(ApplicationError):
    """A frozen Skill set could not be resolved under a valid Run claim."""

    def __init__(self) -> None:
        super().__init__("skill_resolution_failed")


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

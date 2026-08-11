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

from friday.application.memory.ports import (
    MemoryIndexSnapshotRepository,
    MemoryRetrievalRecordRepository,
)
from friday.domain.agent import Agent, AgentRevision, RunAgentResolution, TaskAgentBinding
from friday.domain.approval import ApprovalRequest
from friday.domain.artifact import Artifact
from friday.domain.conversation import Conversation
from friday.domain.conversation_turn import ConversationTurn
from friday.domain.delegation import DelegationRequest
from friday.domain.delivery_attempt import DeliveryAttempt, DeliveryAttemptOutcome
from friday.domain.event import RunEvent, RunEventType
from friday.domain.identifiers import (
    AgentId,
    AgentRevisionId,
    ApprovalRequestId,
    ArtifactId,
    ConversationId,
    ConversationTurnId,
    DelegationRequestId,
    DeliveryAttemptId,
    DeliveryId,
    RunId,
    RunStepId,
    ScheduleFireId,
    ScheduleId,
    SkillEvaluationRunId,
    SkillEvidenceSnapshotId,
    SkillId,
    SkillImprovementProposalId,
    SkillImprovementWorkId,
    SkillPromotionRequestId,
    SkillRevisionId,
    SkillRollbackRequestId,
    TaskId,
    ToolInvocationId,
)
from friday.domain.outbound_delivery import OutboundDelivery
from friday.domain.run import Run
from friday.domain.schedule import Schedule
from friday.domain.schedule_delivery_policy import ScheduleDeliveryPolicy
from friday.domain.schedule_fire import ScheduleFire
from friday.domain.schedule_fire_delivery_plan import ScheduleFireDeliveryPlan
from friday.domain.skill import (
    RunSkillBinding,
    RunSkillResolution,
    Skill,
    SkillRevision,
    TaskSkillBinding,
)
from friday.domain.skill_evaluation import (
    SkillCandidateEvaluation,
    SkillEvaluationCase,
    SkillEvaluationCaseResult,
    SkillEvaluationRun,
    SkillEvaluationSuite,
)
from friday.domain.skill_evidence_snapshot import SkillEvidenceSnapshot
from friday.domain.skill_improvement import SkillImprovementProposal
from friday.domain.skill_improvement_policy import SkillImprovementPolicy
from friday.domain.skill_improvement_work import SkillImprovementWork
from friday.domain.skill_promotion import SkillPromotionRequest, SkillRollbackRequest
from friday.domain.skill_usage import SkillRunFeedback, SkillUsageRecord
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
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        """Clear ownership/lease fields but keep the row (and its
        claim_generation) claimable again. Returns False on ownership
        mismatch or an already-expired lease."""
        ...

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
        """Release ownership and reschedule availability in one conditional
        UPDATE. Returns False on ownership mismatch or an already-expired
        lease."""
        ...

    def remove_if_claimed(
        self, run_id: RunId, worker_id: str, claim_token: str, claim_generation: int, now: datetime
    ) -> bool:
        """Delete the work item only if still owned by this exact claim and
        its lease has not already expired. Returns False otherwise."""
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


class AgentRepository(Protocol):
    def add(self, agent: Agent) -> None: ...
    def get(self, agent_id: AgentId) -> Agent | None: ...
    def get_by_key(self, key: str) -> Agent | None: ...
    def save(self, agent: Agent) -> None: ...
    def list(self, limit: int) -> list[Agent]: ...


class AgentRevisionRepository(Protocol):
    def add(self, revision: AgentRevision) -> None: ...
    def get(self, revision_id: AgentRevisionId) -> AgentRevision | None: ...
    def list_for_agent(self, agent_id: AgentId) -> list[AgentRevision]: ...
    def next_version(self, agent_id: AgentId) -> int: ...


class TaskAgentBindingRepository(Protocol):
    def get(self, task_id: TaskId) -> TaskAgentBinding | None: ...
    def replace(self, task_id: TaskId, binding: TaskAgentBinding | None) -> None: ...


class RunAgentResolutionRepository(Protocol):
    def get(self, run_id: RunId) -> RunAgentResolution | None: ...
    def add(self, resolution: RunAgentResolution) -> None: ...

    def add_if_claimed(
        self,
        resolution: RunAgentResolution,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool: ...


class DelegationRequestRepository(Protocol):
    def add(self, request: DelegationRequest) -> None: ...
    def save(self, request: DelegationRequest) -> None: ...
    def get(self, delegation_id: DelegationRequestId) -> DelegationRequest | None: ...
    def list_for_run(self, run_id: RunId) -> list[DelegationRequest]: ...
    def count_dispatched_for_run(self, run_id: RunId) -> int: ...
    def get_for_child_execution(self, execution_id: RunId) -> DelegationRequest | None: ...
    def get_for_child_task(self, task_id: TaskId) -> DelegationRequest | None: ...
    def has_dispatched_for_run(self, run_id: RunId) -> bool: ...


class SkillRepository(Protocol):
    def add(self, skill: Skill) -> None: ...
    def get(self, skill_id: SkillId) -> Skill | None: ...
    def get_by_key(self, key: str) -> Skill | None: ...
    def save(self, skill: Skill) -> None: ...
    def list(self, limit: int) -> list[Skill]: ...


class SkillRevisionRepository(Protocol):
    def add(self, revision: SkillRevision) -> None: ...
    def get(self, revision_id: SkillRevisionId) -> SkillRevision | None: ...
    def list_for_skill(self, skill_id: SkillId) -> list[SkillRevision]: ...
    def next_version(self, skill_id: SkillId) -> int: ...


class TaskSkillBindingRepository(Protocol):
    def list_for_task(self, task_id: TaskId) -> list[TaskSkillBinding]: ...
    def replace(self, task_id: TaskId, bindings: list[TaskSkillBinding]) -> None: ...


class RunSkillResolutionRepository(Protocol):
    def get(self, run_id: RunId) -> RunSkillResolution | None: ...
    def add(self, resolution: RunSkillResolution) -> None: ...

    def add_if_claimed(
        self,
        resolution: RunSkillResolution,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool: ...


class RunSkillBindingRepository(Protocol):
    def list_for_run(self, run_id: RunId) -> list[RunSkillBinding]: ...
    def add_all(self, bindings: list[RunSkillBinding]) -> None: ...


class SkillUsageRecordRepository(Protocol):
    def get_for_run_skill(self, run_id: RunId, skill_id: SkillId) -> SkillUsageRecord | None: ...
    def add(self, record: SkillUsageRecord) -> None: ...
    def list_for_skill(self, skill_id: SkillId, limit: int) -> list[SkillUsageRecord]: ...


class SkillRunFeedbackRepository(Protocol):
    def add(self, feedback: SkillRunFeedback) -> None: ...
    def list_for_run_skill(self, run_id: RunId, skill_id: SkillId) -> list[SkillRunFeedback]: ...


class SkillEvaluationSuiteRepository(Protocol):
    def get(self, suite_id: object) -> SkillEvaluationSuite | None: ...
    def add(self, suite: SkillEvaluationSuite) -> None: ...
    def list_for_skill(self, skill_id: SkillId) -> list[SkillEvaluationSuite]: ...


class SkillEvaluationCaseRepository(Protocol):
    def list_for_suite(self, suite_id: object) -> list[SkillEvaluationCase]: ...
    def add(self, case: SkillEvaluationCase) -> None: ...


class SkillEvaluationRunRepository(Protocol):
    def add(self, run: SkillEvaluationRun) -> None: ...
    def get(self, run_id: object) -> SkillEvaluationRun | None: ...


class SkillEvaluationCaseResultRepository(Protocol):
    def add_all(self, results: list[SkillEvaluationCaseResult]) -> None: ...
    def list_for_run(self, run_id: SkillEvaluationRunId) -> list[SkillEvaluationCaseResult]: ...


class SkillCandidateEvaluationRepository(Protocol):
    def add(self, evaluation: SkillCandidateEvaluation) -> None: ...
    def get_for_proposal(
        self, proposal_id: SkillImprovementProposalId
    ) -> SkillCandidateEvaluation | None: ...


class SkillImprovementProposalRepository(Protocol):
    def add(self, proposal: SkillImprovementProposal) -> None: ...
    def get(self, proposal_id: SkillImprovementProposalId) -> SkillImprovementProposal | None: ...
    def save(self, proposal: SkillImprovementProposal) -> None: ...
    def list_for_skill(self, skill_id: SkillId) -> list[SkillImprovementProposal]: ...


class SkillImprovementWorkRepository(Protocol):
    def add(self, work: SkillImprovementWork) -> None: ...
    def add_if_active_absent(self, work: SkillImprovementWork) -> bool: ...
    def get(self, work_id: SkillImprovementWorkId) -> SkillImprovementWork | None: ...
    def get_active_for_skill(self, skill_id: SkillId) -> SkillImprovementWork | None: ...
    def list_due(self, now: datetime, limit: int) -> list[SkillImprovementWork]: ...
    def save(self, work: SkillImprovementWork) -> None: ...

    def claim_for_skill(
        self,
        skill_id: SkillId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> SkillImprovementWork | None: ...

    def save_if_claimed(
        self,
        work: SkillImprovementWork,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool: ...


class SkillEvidenceSnapshotRepository(Protocol):
    def add(self, snapshot: SkillEvidenceSnapshot) -> None: ...
    def get(self, snapshot_id: SkillEvidenceSnapshotId) -> SkillEvidenceSnapshot | None: ...


class SkillImprovementPolicyRepository(Protocol):
    def get(self, skill_id: SkillId) -> SkillImprovementPolicy | None: ...
    def list_enabled(self, limit: int) -> list[SkillImprovementPolicy]: ...
    def list_due_enabled(self, now: datetime, limit: int) -> list[SkillImprovementPolicy]: ...
    def save(self, policy: SkillImprovementPolicy) -> None: ...


class SkillPromotionRequestRepository(Protocol):
    def add(self, request: SkillPromotionRequest) -> None: ...
    def get(self, request_id: SkillPromotionRequestId) -> SkillPromotionRequest | None: ...
    def save(self, request: SkillPromotionRequest) -> None: ...


class SkillRollbackRequestRepository(Protocol):
    def add(self, request: SkillRollbackRequest) -> None: ...
    def get(self, request_id: SkillRollbackRequestId) -> SkillRollbackRequest | None: ...
    def save(self, request: SkillRollbackRequest) -> None: ...


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

    def has_non_terminal_for_ids(self, run_ids: list[RunId]) -> bool: ...
    def count_for_execution(self, execution_id: RunId) -> int: ...
    def ordinal_for_execution(self, run_id: RunId) -> int: ...
    def list_for_execution(self, execution_id: RunId) -> list[Run]:
        """Ordered by created_at, then id. Returns all runs sharing execution_id."""
        ...

    def get_latest_for_execution(self, execution_id: RunId) -> Run | None:
        """The most recently created run sharing execution_id (ties broken by
        id), i.e. the effective run of a retry chain. Bounded to one row."""
        ...


class RunStepRepository(Protocol):
    def add(self, step: RunStep) -> None: ...
    def get(self, step_id: RunStepId) -> RunStep | None: ...
    def save(self, step: RunStep) -> None: ...

    def list_for_run(self, run_id: RunId) -> list[RunStep]:
        """Ordered by position, then id."""
        ...

    def has_non_terminal_for_run(self, run_id: RunId) -> bool: ...

    def list_for_run_page(
        self, run_id: RunId, limit: int, after_position: int | None, after_id: str | None
    ) -> list[RunStep]: ...


class ScheduleRepository(Protocol):
    def add(self, schedule: Schedule) -> None: ...
    def get(self, schedule_id: ScheduleId) -> Schedule | None: ...
    def save(self, schedule: Schedule) -> None: ...
    def list_for_task(self, task_id: TaskId, limit: int) -> list[Schedule]: ...
    def list_for_task_page(
        self, task_id: TaskId, limit: int, after_created_at: datetime | None, after_id: str | None
    ) -> list[Schedule]: ...
    def list_due(self, now: datetime, limit: int) -> list[Schedule]: ...
    def complete_for_task(self, task_id: TaskId, at: datetime, *, cancelled: bool) -> None: ...


class ScheduleFireRepository(Protocol):
    def add(self, fire: ScheduleFire) -> None: ...
    def list_for_schedule(self, schedule_id: ScheduleId, limit: int) -> list[ScheduleFire]: ...
    def list_for_schedule_page(
        self,
        schedule_id: ScheduleId,
        limit: int,
        after_scheduled_for: datetime | None,
        after_id: str | None,
    ) -> list[ScheduleFire]: ...
    def has_non_terminal_execution_for_schedule(self, schedule_id: ScheduleId) -> bool: ...


class ScheduleDeliveryPolicyRepository(Protocol):
    def get_for_schedule(self, schedule_id: ScheduleId) -> ScheduleDeliveryPolicy | None: ...
    def put_for_nonterminal_schedule(self, policy: ScheduleDeliveryPolicy) -> bool: ...


class ScheduleFireDeliveryPlanRepository(Protocol):
    def add_for_fire(self, plan: ScheduleFireDeliveryPlan, fire: ScheduleFire) -> None: ...
    def get_by_fire(self, schedule_fire_id: ScheduleFireId) -> ScheduleFireDeliveryPlan | None: ...
    def list_ready_without_delivery(self, limit: int) -> list[ScheduleFireId]: ...
    def mark_content_rejected(self, schedule_fire_id: ScheduleFireId, run_id: RunId) -> bool: ...


class ConversationRepository(Protocol):
    def add(self, conversation: Conversation) -> None: ...
    def get(self, conversation_id: ConversationId) -> Conversation | None: ...
    def save(self, conversation: Conversation) -> None: ...


class ConversationTurnRepository(Protocol):
    def add(self, turn: ConversationTurn) -> None: ...
    def get(self, turn_id: ConversationTurnId) -> ConversationTurn | None: ...
    def get_by_client_turn_id(
        self, conversation_id: ConversationId, client_turn_id: str
    ) -> ConversationTurn | None: ...
    def get_by_run(self, run_id: RunId) -> ConversationTurn | None: ...
    def list_for_conversation_page(
        self,
        conversation_id: ConversationId,
        limit: int,
        after_created_at: datetime | None,
        after_id: str | None,
    ) -> list[ConversationTurn]: ...
    def list_recent_before(
        self,
        conversation_id: ConversationId,
        before_created_at: datetime | None,
        before_id: str | None,
        limit: int,
    ) -> list[ConversationTurn]: ...


class ApprovalRepository(Protocol):
    def add(self, approval: ApprovalRequest) -> None: ...
    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None: ...
    def save(self, approval: ApprovalRequest) -> None: ...

    def consume_if_unconsumed(self, approval_id: ApprovalRequestId, at: datetime) -> bool: ...

    def list_pending_for_run(self, run_id: RunId) -> list[ApprovalRequest]:
        """Ordered by requested_at, then id."""
        ...

    def has_pending_for_run(self, run_id: RunId) -> bool: ...

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

    def has_non_terminal_for_run(self, run_id: RunId) -> bool: ...

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
    def latest_of_type_for_run(
        self, run_id: RunId, event_type: RunEventType
    ) -> RunEvent | None: ...

    def list_for_run(self, run_id: RunId) -> list[RunEvent]:
        """Ordered by sequence."""
        ...

    def list_after_sequence(
        self, run_id: RunId, after_sequence: int, limit: int
    ) -> list[RunEvent]: ...

    def reserve_sequences(self, run_id: RunId, count: int) -> int:
        """Atomically reserve a sequence block; count must be >= 1."""
        ...


class OutboundDeliveryRepository(Protocol):
    def add(self, delivery: OutboundDelivery) -> None: ...
    def get(self, delivery_id: DeliveryId) -> OutboundDelivery | None: ...
    def get_by_source_tool_invocation_id(
        self, invocation_id: ToolInvocationId
    ) -> OutboundDelivery | None: ...
    def get_by_source_schedule_fire_id(
        self, schedule_fire_id: ScheduleFireId
    ) -> OutboundDelivery | None: ...

    def list_due(self, now: datetime, limit: int) -> list[OutboundDelivery]:
        """Read-only QUEUED selection, ordered by available_at then id."""
        ...

    def find_expired_claims(self, now: datetime, limit: int) -> list[OutboundDelivery]:
        """Read-only SENDING selection whose claim lease has expired."""
        ...

    def try_claim(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> int | None:
        """Atomically claim a due QUEUED delivery.

        Returns the new claim_generation when exactly one row moved to
        SENDING, else None. Must be one fenced UPDATE, not read-then-write.
        """
        ...

    def is_claim_active(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        """Durably verify an exact, unexpired SENDING claim. Fails closed."""
        ...

    def mark_dispatch_started(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        """Cross the durable external side-effect boundary exactly once."""
        ...

    def save_claimed_lifecycle(
        self,
        delivery: OutboundDelivery,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
    ) -> bool:
        """Persist lifecycle fields only, fenced by an exact unexpired claim.

        Authority, content and source columns must never be written here.
        """
        ...

    def requeue_expired_pre_dispatch(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        available_at: datetime,
    ) -> bool:
        """Requeue an expired claim with dispatch_started_at IS NULL."""
        ...

    def fail_expired_pre_dispatch(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        """Terminally fail an expired pre-dispatch claim out of retry budget."""
        ...

    def mark_expired_post_dispatch_ambiguous(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        failure_code: str,
        failure_message: str,
    ) -> bool:
        """Park an expired claim with dispatch_started_at IS NOT NULL."""
        ...


#: Hard ceiling on one bounded attempt-history read. The ledger is an audit
#: trail, so a caller must always name how much of it it wants; there is no
#: "read everything" mode to accidentally rely on.
MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT = 1000


def validate_delivery_attempt_history_limit(limit: int) -> int:
    """Return `limit` if it is within `1..MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT`.

    Zero, negative, and over-max values are rejected rather than clamped. A
    negative LIMIT is *unbounded* in SQLite, so silently passing one through
    would turn a bounded audit read into a full-table scan; clamping instead of
    raising would hide the caller's bug.
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("delivery attempt history limit must be an integer")
    if limit < 1 or limit > MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT:
        raise ValueError(
            "delivery attempt history limit must be between 1 and "
            f"{MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT}, got {limit}"
        )
    return limit


class DeliveryAttemptRepository(Protocol):
    """Claim-fenced audit ledger. Deliberately has no generic `add`/`save`.

    A durable attempt row means "Friday crossed the external boundary for this
    exact claim generation". Allowing an unrestricted insert would let any
    caller manufacture that statement, so the only creation path is
    `begin_for_claim`, whose fencing is enforced inside the database.
    """

    def begin_for_claim(
        self,
        attempt_id: DeliveryAttemptId,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        started_at: datetime,
        now: datetime,
    ) -> bool:
        """Create one IN_PROGRESS attempt, fenced on an exact active claim.

        Returns false — writing nothing — unless the delivery is SENDING, owned
        by this exact (worker_id, claim_token, claim_generation), its lease is
        unexpired, and `dispatch_started_at` already equals `started_at`.
        """
        ...

    def get_for_generation(
        self, delivery_id: DeliveryId, claim_generation: int
    ) -> DeliveryAttempt | None: ...
    def list_for_delivery(self, delivery_id: DeliveryId, limit: int) -> list[DeliveryAttempt]:
        """Newest first by (started_at DESC, id DESC); bounded by `limit`.

        `limit` must satisfy `1 <= limit <= MAX_DELIVERY_ATTEMPT_HISTORY_LIMIT`.
        """
        ...

    def complete_for_claim(
        self,
        delivery_id: DeliveryId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
        now: datetime,
        outcome: DeliveryAttemptOutcome,
        failure_code: str | None,
    ) -> bool: ...
    def close_expired_as_ambiguous(
        self,
        delivery_id: DeliveryId,
        claim_generation: int,
        now: datetime,
        failure_code: str,
    ) -> bool: ...


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
    def agents(self) -> AgentRepository: ...
    @property
    def agent_revisions(self) -> AgentRevisionRepository: ...
    @property
    def task_agent_bindings(self) -> TaskAgentBindingRepository: ...
    @property
    def run_agent_resolutions(self) -> RunAgentResolutionRepository: ...
    @property
    def delegation_requests(self) -> DelegationRequestRepository: ...
    @property
    def skills(self) -> SkillRepository: ...
    @property
    def skill_revisions(self) -> SkillRevisionRepository: ...
    @property
    def task_skill_bindings(self) -> TaskSkillBindingRepository: ...
    @property
    def run_skill_resolutions(self) -> RunSkillResolutionRepository: ...
    @property
    def run_skill_bindings(self) -> RunSkillBindingRepository: ...
    @property
    def skill_usage_records(self) -> SkillUsageRecordRepository: ...
    @property
    def skill_improvement_proposals(self) -> SkillImprovementProposalRepository: ...
    @property
    def skill_improvement_work(self) -> SkillImprovementWorkRepository: ...
    @property
    def skill_evidence_snapshots(self) -> SkillEvidenceSnapshotRepository: ...
    @property
    def skill_improvement_policies(self) -> SkillImprovementPolicyRepository: ...
    @property
    def skill_promotion_requests(self) -> SkillPromotionRequestRepository: ...
    @property
    def skill_rollback_requests(self) -> SkillRollbackRequestRepository: ...
    @property
    def skill_run_feedback(self) -> SkillRunFeedbackRepository: ...
    @property
    def skill_evaluation_suites(self) -> SkillEvaluationSuiteRepository: ...
    @property
    def skill_evaluation_cases(self) -> SkillEvaluationCaseRepository: ...
    @property
    def skill_evaluation_runs(self) -> SkillEvaluationRunRepository: ...
    @property
    def skill_evaluation_case_results(self) -> SkillEvaluationCaseResultRepository: ...
    @property
    def skill_candidate_evaluations(self) -> SkillCandidateEvaluationRepository: ...
    @property
    def runs(self) -> RunRepository: ...
    @property
    def schedules(self) -> ScheduleRepository: ...
    @property
    def conversations(self) -> ConversationRepository: ...
    @property
    def conversation_turns(self) -> ConversationTurnRepository: ...
    @property
    def schedule_fires(self) -> ScheduleFireRepository: ...
    @property
    def schedule_delivery_policies(self) -> ScheduleDeliveryPolicyRepository: ...
    @property
    def schedule_fire_delivery_plans(self) -> ScheduleFireDeliveryPlanRepository: ...
    @property
    def steps(self) -> RunStepRepository: ...
    @property
    def approvals(self) -> ApprovalRepository: ...
    @property
    def artifacts(self) -> ArtifactRepository: ...
    @property
    def tool_invocations(self) -> ToolInvocationRepository: ...
    @property
    def deliveries(self) -> OutboundDeliveryRepository: ...
    @property
    def delivery_attempts(self) -> DeliveryAttemptRepository: ...
    @property
    def events(self) -> RunEventStore: ...
    @property
    def task_events(self) -> TaskEventStore: ...
    @property
    def work_queue(self) -> RunWorkQueue: ...
    @property
    def memory_index_snapshots(self) -> MemoryIndexSnapshotRepository: ...
    @property
    def memory_retrieval_records(self) -> MemoryRetrievalRecordRepository: ...

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

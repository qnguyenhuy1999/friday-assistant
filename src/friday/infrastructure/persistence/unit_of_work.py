"""SQLAlchemy-backed `UnitOfWork` (see `friday.application.ports.UnitOfWork`).

One `Session` is shared by every repository exposed here; this class is the
sole place that calls `session.commit()`/`session.rollback()`/`session.close()`,
so no repository can commit independently of the transaction boundary. The
application-facing protocol never exposes the session itself.

Persistence failures are translated into the stable application error
hierarchy at this boundary — `IntegrityError`/`OperationalError`/
`StaleDataError` must never escape into application or use-case code.
"""

from __future__ import annotations

import contextlib
from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from friday.application.errors import (
    ApplicationError,
    ConcurrencyConflict,
    EntityConflict,
    TransactionFailure,
)
from friday.application.ports import UnitOfWork, UnitOfWorkFactory
from friday.infrastructure.persistence.repositories import (
    AgentRepository,
    AgentRevisionRepository,
    ApprovalRepository,
    ArtifactRepository,
    ConversationRepository,
    ConversationTurnRepository,
    DelegationRequestRepository,
    DeliveryAttemptRepository,
    MemoryIndexSnapshotRepository,
    MemoryRetrievalRecordRepository,
    OutboundDeliveryRepository,
    RunAgentResolutionRepository,
    RunEventStore,
    RunRepository,
    RunSkillBindingRepository,
    RunSkillResolutionRepository,
    RunStepRepository,
    ScheduleDeliveryPolicyRepository,
    ScheduleFireDeliveryPlanRepository,
    ScheduleFireRepository,
    ScheduleRepository,
    SkillCandidateEvaluationRepository,
    SkillEvaluationCaseRepository,
    SkillEvaluationCaseResultRepository,
    SkillEvaluationRunRepository,
    SkillEvaluationSuiteRepository,
    SkillEvidenceSnapshotRepository,
    SkillImprovementPolicyRepository,
    SkillImprovementProposalRepository,
    SkillImprovementWorkRepository,
    SkillPromotionRequestRepository,
    SkillRepository,
    SkillRevisionRepository,
    SkillRollbackRequestRepository,
    SkillRunFeedbackRepository,
    SkillUsageRecordRepository,
    TaskAgentBindingRepository,
    TaskEventStore,
    TaskRepository,
    TaskSkillBindingRepository,
    ToolInvocationRepository,
)
from friday.infrastructure.persistence.work_queue import SqlAlchemyRunWorkQueue


def _translated(exc: SQLAlchemyError) -> ApplicationError:
    """Map a SQLAlchemy failure onto the stable application error hierarchy.
    Messages are static; the original exception stays chained internally."""
    if isinstance(exc, StaleDataError):
        return ConcurrencyConflict("write lost an optimistic-concurrency race")
    if isinstance(exc, IntegrityError):
        return EntityConflict("write violated a uniqueness or state constraint")
    return TransactionFailure("database transaction failed")


class SqlAlchemyUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._tasks = TaskRepository(session)
        self._agents = AgentRepository(session)
        self._agent_revisions = AgentRevisionRepository(session)
        self._task_agent_bindings = TaskAgentBindingRepository(session)
        self._run_agent_resolutions = RunAgentResolutionRepository(session)
        self._delegation_requests = DelegationRequestRepository(session)
        self._skills = SkillRepository(session)
        self._skill_revisions = SkillRevisionRepository(session)
        self._task_skill_bindings = TaskSkillBindingRepository(session)
        self._run_skill_resolutions = RunSkillResolutionRepository(session)
        self._run_skill_bindings = RunSkillBindingRepository(session)
        self._skill_usage_records = SkillUsageRecordRepository(session)
        self._skill_run_feedback = SkillRunFeedbackRepository(session)
        self._skill_evaluation_suites = SkillEvaluationSuiteRepository(session)
        self._skill_evaluation_cases = SkillEvaluationCaseRepository(session)
        self._skill_evaluation_runs = SkillEvaluationRunRepository(session)
        self._skill_evaluation_case_results = SkillEvaluationCaseResultRepository(session)
        self._skill_candidate_evaluations = SkillCandidateEvaluationRepository(session)
        self._skill_improvement_proposals = SkillImprovementProposalRepository(session)
        self._skill_improvement_work = SkillImprovementWorkRepository(session)
        self._skill_evidence_snapshots = SkillEvidenceSnapshotRepository(session)
        self._skill_improvement_policies = SkillImprovementPolicyRepository(session)
        self._skill_promotion_requests = SkillPromotionRequestRepository(session)
        self._skill_rollback_requests = SkillRollbackRequestRepository(session)
        self._runs = RunRepository(session)
        self._schedules = ScheduleRepository(session)
        self._conversations = ConversationRepository(session)
        self._conversation_turns = ConversationTurnRepository(session)
        self._schedule_fires = ScheduleFireRepository(session)
        self._schedule_delivery_policies = ScheduleDeliveryPolicyRepository(session)
        self._schedule_fire_delivery_plans = ScheduleFireDeliveryPlanRepository(session)
        self._steps = RunStepRepository(session)
        self._approvals = ApprovalRepository(session)
        self._artifacts = ArtifactRepository(session)
        self._tool_invocations = ToolInvocationRepository(session)
        self._deliveries = OutboundDeliveryRepository(session)
        self._delivery_attempts = DeliveryAttemptRepository(session)
        self._events = RunEventStore(session)
        self._task_events = TaskEventStore(session)
        self._work_queue = SqlAlchemyRunWorkQueue(session)
        self._memory_index_snapshots = MemoryIndexSnapshotRepository(session)
        self._memory_retrieval_records = MemoryRetrievalRecordRepository(session)

    @property
    def tasks(self) -> TaskRepository:
        return self._tasks

    @property
    def agents(self) -> AgentRepository:
        return self._agents

    @property
    def agent_revisions(self) -> AgentRevisionRepository:
        return self._agent_revisions

    @property
    def task_agent_bindings(self) -> TaskAgentBindingRepository:
        return self._task_agent_bindings

    @property
    def run_agent_resolutions(self) -> RunAgentResolutionRepository:
        return self._run_agent_resolutions

    @property
    def delegation_requests(self) -> DelegationRequestRepository:
        return self._delegation_requests

    @property
    def skills(self) -> SkillRepository:
        return self._skills

    @property
    def skill_revisions(self) -> SkillRevisionRepository:
        return self._skill_revisions

    @property
    def task_skill_bindings(self) -> TaskSkillBindingRepository:
        return self._task_skill_bindings

    @property
    def run_skill_resolutions(self) -> RunSkillResolutionRepository:
        return self._run_skill_resolutions

    @property
    def run_skill_bindings(self) -> RunSkillBindingRepository:
        return self._run_skill_bindings

    @property
    def skill_usage_records(self) -> SkillUsageRecordRepository:
        return self._skill_usage_records

    @property
    def skill_improvement_proposals(self) -> SkillImprovementProposalRepository:
        return self._skill_improvement_proposals

    @property
    def skill_improvement_work(self) -> SkillImprovementWorkRepository:
        return self._skill_improvement_work

    @property
    def skill_evidence_snapshots(self) -> SkillEvidenceSnapshotRepository:
        return self._skill_evidence_snapshots

    @property
    def skill_improvement_policies(self) -> SkillImprovementPolicyRepository:
        return self._skill_improvement_policies

    @property
    def skill_promotion_requests(self) -> SkillPromotionRequestRepository:
        return self._skill_promotion_requests

    @property
    def skill_rollback_requests(self) -> SkillRollbackRequestRepository:
        return self._skill_rollback_requests

    @property
    def skill_run_feedback(self) -> SkillRunFeedbackRepository:
        return self._skill_run_feedback

    @property
    def skill_evaluation_suites(self) -> SkillEvaluationSuiteRepository:
        return self._skill_evaluation_suites

    @property
    def skill_evaluation_cases(self) -> SkillEvaluationCaseRepository:
        return self._skill_evaluation_cases

    @property
    def skill_evaluation_runs(self) -> SkillEvaluationRunRepository:
        return self._skill_evaluation_runs

    @property
    def skill_evaluation_case_results(self) -> SkillEvaluationCaseResultRepository:
        return self._skill_evaluation_case_results

    @property
    def skill_candidate_evaluations(self) -> SkillCandidateEvaluationRepository:
        return self._skill_candidate_evaluations

    @property
    def runs(self) -> RunRepository:
        return self._runs

    @property
    def schedules(self) -> ScheduleRepository:
        return self._schedules

    @property
    def conversations(self) -> ConversationRepository:
        return self._conversations

    @property
    def conversation_turns(self) -> ConversationTurnRepository:
        return self._conversation_turns

    @property
    def schedule_fires(self) -> ScheduleFireRepository:
        return self._schedule_fires

    @property
    def schedule_delivery_policies(self) -> ScheduleDeliveryPolicyRepository:
        return self._schedule_delivery_policies

    @property
    def schedule_fire_delivery_plans(self) -> ScheduleFireDeliveryPlanRepository:
        return self._schedule_fire_delivery_plans

    @property
    def steps(self) -> RunStepRepository:
        return self._steps

    @property
    def approvals(self) -> ApprovalRepository:
        return self._approvals

    @property
    def artifacts(self) -> ArtifactRepository:
        return self._artifacts

    @property
    def tool_invocations(self) -> ToolInvocationRepository:
        return self._tool_invocations

    @property
    def deliveries(self) -> OutboundDeliveryRepository:
        return self._deliveries

    @property
    def delivery_attempts(self) -> DeliveryAttemptRepository:
        return self._delivery_attempts

    @property
    def events(self) -> RunEventStore:
        return self._events

    @property
    def task_events(self) -> TaskEventStore:
        return self._task_events

    @property
    def work_queue(self) -> SqlAlchemyRunWorkQueue:
        return self._work_queue

    @property
    def memory_index_snapshots(self) -> MemoryIndexSnapshotRepository:
        return self._memory_index_snapshots

    @property
    def memory_retrieval_records(self) -> MemoryRetrievalRecordRepository:
        return self._memory_retrieval_records

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._rollback_quietly()
        self._session.close()
        if isinstance(exc, SQLAlchemyError):
            # Repository reads/writes inside the block (e.g. autoflush during
            # a query) can raise before commit(); translate here so no
            # SQLAlchemy exception ever crosses into application code.
            raise _translated(exc) from exc

    def commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._rollback_quietly()
            raise _translated(exc) from exc

    def rollback(self) -> None:
        try:
            self._session.rollback()
        except SQLAlchemyError as exc:
            raise TransactionFailure("rollback failed") from exc

    def _rollback_quietly(self) -> None:
        with contextlib.suppress(SQLAlchemyError):
            self._session.rollback()


def create_unit_of_work_factory(session_factory: sessionmaker[Session]) -> UnitOfWorkFactory:
    def factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory())

    return factory

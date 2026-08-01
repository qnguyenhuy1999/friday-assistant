"""Bounded maintenance ticks for worker claims and approval deadlines."""

from __future__ import annotations

import logging
import unicodedata
from typing import cast

from friday.application.approval_workflow import ExpireApproval
from friday.application.commands import ExpireApprovalCommand
from friday.application.errors import EntityConflict
from friday.application.list_events import canonical_final_agent_summary
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.application.results import ApprovalRequestResult
from friday.application.skill_evaluation import (
    BrainOnlySkillEvaluator,
    CompareSkillImprovementProposal,
    DeterministicEvaluatorRegistry,
    RunBrainOnlySkillEvaluation,
)
from friday.application.skill_improvement import (
    BrainOnlyCandidateGenerator,
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
    GenerateSkillImprovementProposal,
)
from friday.application.skill_improvement_policy import (
    MarkSkillImprovementPolicyTriggered,
    RunSkillImprovementPolicyNow,
)
from friday.domain.event import RunEventType
from friday.domain.identifiers import (
    DeliveryId,
    RunId,
    ScheduleFireId,
    SkillId,
    SkillImprovementProposalId,
)
from friday.domain.json_value import JsonValue
from friday.domain.outbound_delivery import MAX_BODY_LENGTH, DeliverySourceKind, OutboundDelivery
from friday.domain.run import TERMINAL_RUN_STATUSES, RunStatus
from friday.domain.schedule_fire_delivery_plan import (
    ScheduleFireDeliveryContentSource,
    ScheduleFireDeliveryPlanStatus,
)
from friday.domain.skill_improvement import SkillImprovementProposal

logger = logging.getLogger(__name__)


class ScheduledAnswerContentGate:
    """Deterministic, Friday-owned validation for canonical agent summaries."""

    def validate(self, summary: str | None, max_body_chars: int | None) -> str | None:
        if not isinstance(summary, str):
            return None
        normalized = summary.replace("\r\n", "\n").replace("\r", "\n").strip()
        if (
            not normalized
            or not isinstance(max_body_chars, int)
            or isinstance(max_body_chars, bool)
            or max_body_chars <= 0
            or len(normalized) > min(max_body_chars, MAX_BODY_LENGTH)
        ):
            return None
        if any(unicodedata.category(char) == "Cc" and char != "\n" for char in normalized):
            return None
        try:
            normalized.encode("utf-8")
        except UnicodeEncodeError:
            return None
        return normalized


class MaterializeScheduledAnswerDeliveries:
    """Create at most one durable delivery intent per ready schedule fire.

    This use case owns no route resolution, transport, model, or credentials.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size
        self._gate = ScheduledAnswerContentGate()

    def execute(self) -> int:
        with self._uow_factory() as uow:
            fire_ids = uow.schedule_fire_delivery_plans.list_ready_without_delivery(
                self._batch_size
            )
        materialized = 0
        for fire_id in fire_ids:
            try:
                materialized += int(self._materialize_one(fire_id))
            except EntityConflict:
                # The unique source_schedule_fire_id constraint is the final
                # race fence.  A loser has achieved the idempotent outcome.
                continue
            except Exception:  # noqa: BLE001 - candidate faults are isolated
                logger.warning(
                    "scheduler.answer_materialization_candidate_failed",
                    extra={"schedule_fire_id": str(fire_id), "reason_code": "unexpected_error"},
                )
                continue
        return materialized

    def _materialize_one(self, fire_id: ScheduleFireId) -> bool:
        with self._uow_factory() as uow:
            plan = uow.schedule_fire_delivery_plans.get_by_fire(fire_id)
            if plan is None or plan.status is not ScheduleFireDeliveryPlanStatus.READY:
                return False
            if uow.deliveries.get_by_source_schedule_fire_id(plan.schedule_fire_id) is not None:
                return False
            run = uow.runs.get_latest_for_execution(plan.execution_id)
            if run is None or run.execution_id != plan.execution_id:
                return False
            if run.status is not RunStatus.SUCCEEDED:
                return False
            if plan.content_source is not ScheduleFireDeliveryContentSource.FINAL_AGENT_SUMMARY_V1:
                return self._reject_content(uow, plan.schedule_fire_id, run.id)
            event = uow.events.latest_of_type_for_run(run.id, RunEventType.AGENT_FINISHED)
            body = self._gate.validate(
                canonical_final_agent_summary(event), plan.route_max_body_chars
            )
            if body is None or plan.route_fingerprint is None:
                return self._reject_content(uow, plan.schedule_fire_id, run.id)
            now = self._clock.now()
            uow.deliveries.add(
                OutboundDelivery.new(
                    id=DeliveryId.new(),
                    source_kind=DeliverySourceKind.SCHEDULED_RUN_ANSWER,
                    source_run_id=run.id,
                    source_tool_invocation_id=None,
                    source_schedule_fire_id=plan.schedule_fire_id,
                    route_id=plan.route_id,
                    route_fingerprint=plan.route_fingerprint,
                    subject=None,
                    body=body,
                    available_at=now,
                    created_at=now,
                )
            )
            uow.commit()
            return True

    @staticmethod
    def _reject_content(uow: UnitOfWork, fire_id: ScheduleFireId, run_id: RunId) -> bool:
        # A rejection belongs to this effective run only. A later retry in the
        # same execution lineage has a new id and is eligible again.
        if uow.schedule_fire_delivery_plans.mark_content_rejected(fire_id, run_id):
            uow.commit()
        return False


class RecoverExpiredLeases:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size

    def execute(self) -> int:
        with self._uow_factory() as uow:
            now = self._clock.now()
            expired = uow.work_queue.find_expired_claims(now, self._batch_size)
            recovered = 0
            for item in expired:
                run = uow.runs.get(item.run_id)
                if (
                    run is None
                    or run.status in TERMINAL_RUN_STATUSES
                    or run.status is RunStatus.WAITING_FOR_APPROVAL
                ):
                    recovered += int(uow.work_queue.remove_if_lease_expired(item.run_id, now))
                else:
                    recovered += int(uow.work_queue.clear_expired_claim(item.run_id, now))
            uow.commit()
            return recovered


class ExpireDueApprovals:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock, *, batch_size: int) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._batch_size = batch_size

    def execute(self) -> list[ApprovalRequestResult]:
        with self._uow_factory() as uow:
            now = self._clock.now()
            due = uow.approvals.list_due_for_expiry(now, self._batch_size)
            uow.commit()

        expire = ExpireApproval(self._uow_factory, self._clock)
        results: list[ApprovalRequestResult] = []
        for approval in due:
            try:
                results.append(expire.execute(ExpireApprovalCommand(approval.id)))
            except EntityConflict:
                continue
        return results


class EvaluateDueSkillImprovementPolicies:
    """Create inert candidates from frozen, policy-selected evidence only."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        batch_size: int,
        candidate_generator: BrainOnlyCandidateGenerator | None = None,
        candidate_evaluator: BrainOnlySkillEvaluator | None = None,
    ) -> None:
        self._uow_factory, self._clock, self._batch_size = uow_factory, clock, batch_size
        self._candidate_generator = candidate_generator
        self._candidate_evaluator = candidate_evaluator

    def execute(self) -> int:
        with self._uow_factory() as uow:
            policies = uow.skill_improvement_policies.list_enabled(self._batch_size)
            skill_ids = [policy.skill_id for policy in policies]
        due = RunSkillImprovementPolicyNow(self._uow_factory, self._clock)
        completed = 0
        for skill_id in skill_ids:
            try:
                if not due.execute(skill_id, record_trigger=self._candidate_generator is None):
                    continue
                if self._candidate_generator is None:
                    completed += 1
                    continue
                proposal = self._generate_one(skill_id)
                if self._candidate_evaluator is not None:
                    self._evaluate_one(skill_id, proposal.id)
                MarkSkillImprovementPolicyTriggered(self._uow_factory, self._clock).execute(
                    skill_id
                )
                completed += 1
            except Exception:  # noqa: BLE001 - one skill cannot block maintenance
                logger.warning(
                    "skill_improvement.policy_candidate_failed",
                    extra={"skill_id": str(skill_id)},
                )
        return completed

    def _generate_one(self, skill_id: SkillId) -> SkillImprovementProposal:
        with self._uow_factory() as uow:
            policy = uow.skill_improvement_policies.get(skill_id)
            skill = uow.skills.get(skill_id)
            if policy is None or skill is None or skill.active_revision_id is None:
                raise EntityConflict("due policy requires an active skill revision")
            base = uow.skill_revisions.get(skill.active_revision_id)
            if base is None:
                raise EntityConflict("active skill revision was not found")
            usage = [
                item
                for item in uow.skill_usage_records.list_for_skill(
                    skill_id, policy.evidence_window_size
                )
                if item.revision_id == base.id
            ]
            feedback = [
                item
                for usage_item in usage
                for item in uow.skill_run_feedback.list_for_run_skill(usage_item.run_id, skill_id)
                if item.revision_id == base.id
            ]
            evidence_ids = {f"usage:{item.id}" for item in usage}
            evidence_ids.update(f"feedback:{item.id}" for item in feedback)
            evidence = {
                "usage": [
                    {
                        "id": f"usage:{item.id}",
                        "outcome": item.outcome.value,
                        "failure_code": item.failure_code,
                        "tool_call_count": item.tool_call_count,
                        "approval_count": item.approval_count,
                        "duration_ms": item.duration_ms,
                    }
                    for item in usage
                ],
                "feedback": [
                    {"id": f"feedback:{item.id}", "rating": item.rating.value, "note": item.note}
                    for item in feedback
                ],
            }
            feedback_summaries = tuple(
                f"{item.rating.value}:{item.note}"[:4096] for item in feedback
            )
            base_id, base_instructions = base.id, base.instructions
            generator_version = policy.generator_version
        snapshot = CreateSkillEvidenceSnapshot(self._uow_factory, self._clock).execute(
            skill_id=skill_id, base_revision_id=base_id, evidence=cast(JsonValue, evidence)
        )
        assert self._candidate_generator is not None
        return GenerateSkillImprovementProposal(
            self._candidate_generator,
            CreateSkillImprovementProposal(self._uow_factory, self._clock),
        ).execute(
            skill_id=skill_id,
            base_revision_id=base_id,
            trigger_kind="policy",
            evidence_snapshot_id=snapshot.id,
            evidence_snapshot_hash=snapshot.content_sha256,
            evidence_ids=evidence_ids,
            feedback_summaries=feedback_summaries,
            evaluator_summaries=(),
            generator_version=generator_version,
            base_instructions=base_instructions,
        )

    def _evaluate_one(self, skill_id: SkillId, proposal_id: SkillImprovementProposalId) -> None:
        with self._uow_factory() as uow:
            policy = uow.skill_improvement_policies.get(skill_id)
            proposal = uow.skill_improvement_proposals.get(proposal_id)
            if policy is None or proposal is None:
                raise EntityConflict("proposal evaluation inputs disappeared")
            suite_id, base_revision_id = policy.evaluation_suite_id, proposal.base_revision_id
        assert self._candidate_evaluator is not None
        registry = DeterministicEvaluatorRegistry()
        runner = RunBrainOnlySkillEvaluation(
            self._uow_factory, self._clock, registry, self._candidate_evaluator
        )
        baseline = runner.execute(suite_id=suite_id, revision_id=base_revision_id)
        candidate = runner.execute(suite_id=suite_id, proposal_id=proposal_id)
        CompareSkillImprovementProposal(self._uow_factory, self._clock, registry).execute(
            proposal_id=proposal_id,
            baseline_evaluation_run_id=baseline.id,
            candidate_evaluation_run_id=candidate.id,
            comparison_policy_version=policy.comparison_policy_version,
        )

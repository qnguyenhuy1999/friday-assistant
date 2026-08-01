"""Policy configuration and due checks; intentionally no promotion authority."""

from __future__ import annotations

from dataclasses import replace

from friday.application.errors import EntityConflict, SkillNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import SkillId, SkillImprovementPolicy
from friday.domain.skill_improvement import SkillProposalStatus
from friday.domain.skill_usage import SkillFeedbackRating, SkillUsageOutcome


class SaveSkillImprovementPolicy:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, policy: SkillImprovementPolicy) -> SkillImprovementPolicy:
        with self._uow_factory() as uow:
            if uow.skills.get(policy.skill_id) is None:
                raise SkillNotFound(policy.skill_id)
            suite = uow.skill_evaluation_suites.get(policy.evaluation_suite_id)
            if suite is None or suite.skill_id != policy.skill_id:
                raise EntityConflict("policy evaluation suite does not belong to skill")
            current = uow.skill_improvement_policies.get(policy.skill_id)
            saved = replace(
                policy,
                created_at=current.created_at if current else self._clock.now(),
                updated_at=self._clock.now(),
                last_triggered_at=current.last_triggered_at if current else None,
            )
            uow.skill_improvement_policies.save(saved)
            uow.commit()
            return saved


class RunSkillImprovementPolicyNow:
    """Mark a due policy window; a caller may then generate/evaluate separately."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, skill_id: SkillId, *, record_trigger: bool = True) -> bool:
        with self._uow_factory() as uow:
            policy = uow.skill_improvement_policies.get(skill_id)
            if policy is None or not policy.enabled:
                return False
            now = self._clock.now()
            if (
                policy.last_triggered_at
                and (now - policy.last_triggered_at).total_seconds() < policy.cooldown_seconds
            ):
                return False
            usage = uow.skill_usage_records.list_for_skill(skill_id, policy.evidence_window_size)
            failures = sum(x.outcome is SkillUsageOutcome.FAILED for x in usage)
            feedback = [
                item
                for usage_item in usage
                for item in uow.skill_run_feedback.list_for_run_skill(usage_item.run_id, skill_id)
            ]
            harmful = sum(x.rating is SkillFeedbackRating.HARMFUL for x in feedback)
            open_count = sum(
                x.status
                not in {
                    SkillProposalStatus.CANCELLED,
                    SkillProposalStatus.REJECTED,
                    SkillProposalStatus.PROMOTED,
                }
                for x in uow.skill_improvement_proposals.list_for_skill(skill_id)
            )
            if (
                len(usage) < policy.minimum_usage_records
                or failures < policy.minimum_failures
                or harmful < policy.minimum_harmful_feedback
                or open_count >= policy.max_open_proposals
            ):
                return False
            if record_trigger:
                uow.skill_improvement_policies.save(
                    replace(policy, last_triggered_at=now, updated_at=now)
                )
                uow.commit()
            return True


class MarkSkillImprovementPolicyTriggered:
    """Persist the cooldown only after a proposal is durable."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, skill_id: SkillId) -> None:
        with self._uow_factory() as uow:
            policy = uow.skill_improvement_policies.get(skill_id)
            if policy is None:
                return
            now = self._clock.now()
            uow.skill_improvement_policies.save(
                replace(policy, last_triggered_at=now, updated_at=now)
            )
            uow.commit()

"""Materialize observation-only evidence from a terminal frozen-skill Run."""

from __future__ import annotations

from datetime import datetime

from friday.application.errors import EntityConflict, RunNotFound, SkillNotFound
from friday.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from friday.domain import (
    RunId,
    SkillFeedbackRating,
    SkillId,
    SkillRunFeedback,
    SkillRunFeedbackId,
    SkillUsageOutcome,
    SkillUsageRecord,
    SkillUsageRecordId,
)
from friday.domain.run import TERMINAL_RUN_STATUSES, RunStatus


def _outcome(status: RunStatus) -> SkillUsageOutcome:
    return {
        RunStatus.SUCCEEDED: SkillUsageOutcome.SUCCEEDED,
        RunStatus.FAILED: SkillUsageOutcome.FAILED,
        RunStatus.CANCELLED: SkillUsageOutcome.CANCELLED,
    }[status]


class MaterializeSkillUsage:
    """Idempotently record facts from one terminal Run; never attributes cause."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, run_id: RunId) -> list[SkillUsageRecord]:
        with self._uow_factory() as uow:
            existing = materialize_skill_usage_in_uow(uow, run_id, self._clock.now())
            uow.commit()
            return existing


def materialize_skill_usage_in_uow(
    uow: UnitOfWork, run_id: RunId, now: datetime
) -> list[SkillUsageRecord]:
    """Use within the transaction that terminally changes a Run."""
    run = uow.runs.get(run_id)
    if run is None:
        raise RunNotFound(run_id)
    if run.status not in TERMINAL_RUN_STATUSES:
        raise EntityConflict("skill usage is materialized only for terminal runs")
    resolution = uow.run_skill_resolutions.get(run.id)
    if resolution is None:
        return []  # historical unresolved run; never fabricate evidence
    records: list[SkillUsageRecord] = []
    tool_count = len(uow.tool_invocations.list_for_run(run.id))
    approval_count = len(uow.approvals.list_for_run(run.id))
    attempt = uow.runs.count_for_execution(run.execution_id)
    for binding in uow.run_skill_bindings.list_for_run(run.id):
        prior = uow.skill_usage_records.get_for_run_skill(run.id, binding.skill_id)
        if prior is not None:
            records.append(prior)
            continue
        ended = run.ended_at or now
        duration_ms = (
            int((ended - run.started_at).total_seconds() * 1000) if run.started_at else None
        )
        record = SkillUsageRecord(
            id=SkillUsageRecordId.new(),
            run_id=run.id,
            task_id=run.task_id,
            skill_id=binding.skill_id,
            revision_id=binding.revision_id,
            position=binding.position,
            resolution_id=str(resolution.id),
            execution_id=run.execution_id,
            attempt_number=attempt,
            started_at=run.started_at,
            completed_at=ended,
            outcome=_outcome(run.status),
            failure_code=run.failure.code if run.failure else None,
            tool_call_count=tool_count,
            approval_count=approval_count,
            duration_ms=duration_ms,
            created_at=now,
        )
        uow.skill_usage_records.add(record)
        records.append(record)
    return records


class AddSkillRunFeedback:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        *,
        run_id: RunId,
        skill_id: SkillId,
        rating: SkillFeedbackRating,
        note: str,
        created_by: str,
    ) -> SkillRunFeedback:
        with self._uow_factory() as uow:
            if uow.runs.get(run_id) is None:
                raise RunNotFound(run_id)
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound(skill_id)
            bindings = uow.run_skill_bindings.list_for_run(run_id)
            binding = next((x for x in bindings if x.skill_id == skill_id), None)
            if binding is None:
                raise EntityConflict("feedback requires a frozen skill binding")
            feedback = SkillRunFeedback(
                id=SkillRunFeedbackId.new(),
                run_id=run_id,
                skill_id=skill_id,
                revision_id=binding.revision_id,
                rating=rating,
                note=note,
                created_by=created_by,
                created_at=self._clock.now(),
            )
            uow.skill_run_feedback.add(feedback)
            uow.commit()
            return feedback

from __future__ import annotations

from friday.application.errors import (
    ClaimLost,
    EntityConflict,
    RunNotFound,
    SkillNotFound,
    SkillRevisionNotFound,
    TaskNotFound,
)
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    MAX_SKILLS_PER_TASK,
    RunId,
    RunSkillBinding,
    RunSkillResolution,
    RunSkillResolutionId,
    Skill,
    SkillId,
    SkillRevision,
    SkillRevisionId,
    SkillRevisionSourceKind,
    SkillStatus,
    TaskId,
    TaskSkillBinding,
)


class CreateSkill:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, key: str, display_name: str, description: str) -> Skill:
        skill = Skill.new(
            id=SkillId.new(),
            key=key,
            display_name=display_name,
            description=description,
            created_at=self._clock.now(),
        )
        with self._uow_factory() as uow:
            uow.skills.add(skill)
            uow.commit()
        return skill


class CreateSkillRevision:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self, *, skill_id: SkillId, instructions: str, source_kind: SkillRevisionSourceKind
    ) -> SkillRevision:
        with self._uow_factory() as uow:
            skill = uow.skills.get(skill_id)
            if skill is None:
                raise SkillNotFound(skill_id)
            if skill.status is SkillStatus.ARCHIVED:
                raise EntityConflict("archived skill cannot receive revisions")
            if source_kind is SkillRevisionSourceKind.GENERATED:
                raise EntityConflict("generated revisions can only be created by promotion")
            revision = SkillRevision.new(
                id=SkillRevisionId.new(),
                skill_id=skill_id,
                version=uow.skill_revisions.next_version(skill_id),
                instructions=instructions,
                source_kind=source_kind,
                created_at=self._clock.now(),
            )
            uow.skill_revisions.add(revision)
            uow.commit()
            return revision


class ActivateSkillRevision:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, skill_id: SkillId, revision_id: SkillRevisionId) -> Skill:
        with self._uow_factory() as uow:
            skill = uow.skills.get(skill_id)
            if skill is None:
                raise SkillNotFound(skill_id)
            revision = uow.skill_revisions.get(revision_id)
            if revision is None:
                raise SkillRevisionNotFound(revision_id)
            if revision.skill_id != skill_id:
                # Preserve the domain validation category for a cross-Skill
                # pointer; transport maps this malformed relationship to 422.
                skill.activate(revision, self._clock.now())
            if revision.source_kind is SkillRevisionSourceKind.GENERATED:
                raise EntityConflict("generated revisions require approved promotion")
            current_revision = (
                uow.skill_revisions.get(skill.active_revision_id)
                if skill.active_revision_id is not None
                else None
            )
            if current_revision is not None and revision.version <= current_revision.version:
                raise EntityConflict("historical revisions require approved rollback")
            skill.activate(revision, self._clock.now())
            uow.skills.save(skill)
            uow.commit()
            return skill


class _SkillLifecycle:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def _change(self, skill_id: SkillId, action: str) -> Skill:
        with self._uow_factory() as uow:
            skill = uow.skills.get(skill_id)
            if skill is None:
                raise SkillNotFound(skill_id)
            getattr(skill, action)(self._clock.now())
            uow.skills.save(skill)
            uow.commit()
            return skill


class DisableSkill(_SkillLifecycle):
    def execute(self, skill_id: SkillId) -> Skill:
        return self._change(skill_id, "disable")


class ArchiveSkill(_SkillLifecycle):
    def execute(self, skill_id: SkillId) -> Skill:
        return self._change(skill_id, "archive")


class GetSkill:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, skill_id: SkillId) -> Skill:
        with self._uow_factory() as uow:
            skill = uow.skills.get(skill_id)
            if skill is None:
                raise SkillNotFound(skill_id)
            return skill

    def list_revisions(self, skill_id: SkillId) -> list[SkillRevision]:
        with self._uow_factory() as uow:
            if uow.skills.get(skill_id) is None:
                raise SkillNotFound(skill_id)
            return uow.skill_revisions.list_for_skill(skill_id)


class ReplaceTaskSkills:
    """Atomically replace an operator-controlled Task's ordered skill bindings."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(self, *, task_id: TaskId, skill_ids: list[SkillId]) -> list[TaskSkillBinding]:
        if len(skill_ids) > MAX_SKILLS_PER_TASK or len(set(skill_ids)) != len(skill_ids):
            raise EntityConflict("skill_ids must be unique and within the task skill limit")
        with self._uow_factory() as uow:
            if uow.tasks.get(task_id) is None:
                raise TaskNotFound(task_id)
            bindings: list[TaskSkillBinding] = []
            for position, skill_id in enumerate(skill_ids, start=1):
                skill = uow.skills.get(skill_id)
                if skill is None:
                    raise SkillNotFound(skill_id)
                if skill.status is not SkillStatus.ACTIVE or skill.active_revision_id is None:
                    raise EntityConflict(
                        "task bindings require active skills with an active revision"
                    )
                bindings.append(TaskSkillBinding(task_id, skill_id, position, self._clock.now()))
            uow.task_skill_bindings.replace(task_id, bindings)
            uow.commit()
            return bindings


class ResolveRunSkills:
    """Freeze a Run once. Retries inherit an already-frozen execution exactly."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory, self._clock = uow_factory, clock

    def execute(
        self,
        run_id: RunId,
        worker_id: str,
        claim_token: str,
        claim_generation: int,
    ) -> list[RunSkillBinding]:
        """Resolve once under the exact active worker claim.

        The claim fencing triple is required.  A caller without an exact active
        queue claim cannot publish a resolution and cannot use this production
        use case as a read-through resolver for an already-frozen one.  Missing,
        mismatched, or expired queue state fails closed with ``ClaimLost``.
        Resolution never scans an execution lineage: retries copy only their
        exact source marker/bindings in ``RetryFailedRun``.
        """

        try:
            with self._uow_factory() as uow:
                run = uow.runs.get(run_id)
                if run is None:
                    raise RunNotFound(run_id)
                if not uow.work_queue.is_claim_active(
                    run.id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("skill resolution requires an exact active worker claim")
                existing = uow.run_skill_resolutions.get(run.id)
                if existing is not None:
                    return uow.run_skill_bindings.list_for_run(run.id)

                bindings: list[RunSkillBinding] = []
                for task_binding in uow.task_skill_bindings.list_for_task(run.task_id):
                    skill = uow.skills.get(task_binding.skill_id)
                    if (
                        skill is None
                        or skill.status is not SkillStatus.ACTIVE
                        or skill.active_revision_id is None
                    ):
                        raise EntityConflict("bound skill is no longer resolvable")
                    bindings.append(
                        RunSkillBinding(
                            run.id, skill.id, skill.active_revision_id, task_binding.position
                        )
                    )

                resolution = RunSkillResolution(
                    RunSkillResolutionId.new(), run.id, self._clock.now()
                )
                # The atomic conditional INSERT is the sole publication
                # boundary: it re-reads the queue row under the exact claim so
                # a claim that lapsed after the read above can never publish
                # the freeze marker, and a missing queue row inserts nothing.
                if not uow.run_skill_resolutions.add_if_claimed(
                    resolution,
                    worker_id,
                    claim_token,
                    claim_generation,
                    self._clock.now(),
                ):
                    if uow.run_skill_resolutions.get(run.id) is not None:
                        raise EntityConflict("another resolver won the freeze race")
                    raise ClaimLost("skill resolution claim is stale or expired")
                # Marker and every binding share this transaction.  An empty
                # list is intentional and is still a durable resolved state.
                uow.run_skill_bindings.add_all(bindings)
                if not uow.work_queue.is_claim_active(
                    run.id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("skill resolution claim expired before commit")
                uow.commit()
                return bindings
        except EntityConflict:
            # Two valid resolvers may race on the unique resolution marker.
            # The loser rolls back and reloads the winner; ordinary uniqueness
            # is not a Run failure and can never expose partial bindings.  The
            # winner is readable only while the current caller still holds the
            # exact active claim.
            with self._uow_factory() as uow:
                if not uow.work_queue.is_claim_active(
                    run_id, worker_id, claim_token, claim_generation, self._clock.now()
                ):
                    raise ClaimLost("skill resolution claim is stale or expired") from None
                winner = uow.run_skill_resolutions.get(run_id)
                if winner is not None:
                    return uow.run_skill_bindings.list_for_run(run_id)
            raise

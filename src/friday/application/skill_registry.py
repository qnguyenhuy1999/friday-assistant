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
        worker_id: str | None = None,
        claim_token: str | None = None,
        claim_generation: int | None = None,
    ) -> list[RunSkillBinding]:
        """Resolve once under the exact active worker claim.

        The optional claim arguments retain compatibility with the small
        in-memory application fakes used by the pre-Phase-20 unit suite.  A
        real queued Run must always provide all three; the processor does so.
        Resolution never scans an execution lineage: retries copy only their
        exact source marker/bindings in ``RetryFailedRun``.
        """

        supplied_claim = any(
            value is not None for value in (worker_id, claim_token, claim_generation)
        )
        if supplied_claim and (
            worker_id is None or claim_token is None or claim_generation is None
        ):
            raise ClaimLost("skill resolution requires an exact worker claim")

        try:
            with self._uow_factory() as uow:
                run = uow.runs.get(run_id)
                if run is None:
                    raise RunNotFound(run_id)
                # A production resolution repository exposes the conditional
                # insert below.  In that implementation every resolution is
                # claim-fenced, including a Run whose queue row is missing.
                # The deliberately smaller in-memory test repository retains
                # its legacy no-claim path for pure domain tests.
                add_if_claimed = getattr(uow.run_skill_resolutions, "add_if_claimed", None)
                if (
                    not supplied_claim
                    and callable(add_if_claimed)
                    and uow.work_queue.get(run.id) is not None
                ):
                    raise ClaimLost("skill resolution requires an active worker claim")
                if supplied_claim:
                    assert worker_id is not None
                    assert claim_token is not None
                    assert claim_generation is not None
                    if not uow.work_queue.is_claim_active(
                        run.id, worker_id, claim_token, claim_generation, self._clock.now()
                    ):
                        raise ClaimLost("skill resolution claim is stale or expired")
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
                if supplied_claim and callable(add_if_claimed):
                    assert worker_id is not None
                    assert claim_token is not None
                    assert claim_generation is not None
                    if not add_if_claimed(
                        resolution,
                        worker_id,
                        claim_token,
                        claim_generation,
                        self._clock.now(),
                    ):
                        if uow.run_skill_resolutions.get(run.id) is not None:
                            raise EntityConflict("another resolver won the freeze race")
                        raise ClaimLost("skill resolution claim is stale or expired")
                else:
                    uow.run_skill_resolutions.add(resolution)
                # Marker and every binding share this transaction.  An empty
                # list is intentional and is still a durable resolved state.
                uow.run_skill_bindings.add_all(bindings)
                if supplied_claim:
                    assert worker_id is not None
                    assert claim_token is not None
                    assert claim_generation is not None
                    if not uow.work_queue.is_claim_active(
                        run.id, worker_id, claim_token, claim_generation, self._clock.now()
                    ):
                        raise ClaimLost("skill resolution claim expired before commit")
                uow.commit()
                return bindings
        except EntityConflict:
            # Two valid resolvers may race on the unique resolution marker.
            # The loser rolls back and reloads the winner; ordinary uniqueness
            # is not a Run failure and can never expose partial bindings.
            with self._uow_factory() as uow:
                winner = uow.run_skill_resolutions.get(run_id)
                if winner is not None:
                    return uow.run_skill_bindings.list_for_run(run_id)
            raise

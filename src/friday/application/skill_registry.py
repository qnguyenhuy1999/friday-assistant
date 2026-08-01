from __future__ import annotations

from friday.application.errors import (
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

    def execute(self, run_id: RunId) -> list[RunSkillBinding]:
        with self._uow_factory() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise RunNotFound(run_id)
            if uow.run_skill_resolutions.get(run.id) is not None:
                return uow.run_skill_bindings.list_for_run(run.id)
            inherited: list[RunSkillBinding] | None = None
            for ancestor in uow.runs.list_for_execution(run.execution_id):
                if ancestor.id == run.id:
                    continue
                if uow.run_skill_resolutions.get(ancestor.id) is not None:
                    inherited = uow.run_skill_bindings.list_for_run(ancestor.id)
                    break
            if inherited is None:
                bindings = []
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
            else:
                bindings = [
                    RunSkillBinding(run.id, x.skill_id, x.revision_id, x.position)
                    for x in inherited
                ]
            uow.run_skill_resolutions.add(
                RunSkillResolution(RunSkillResolutionId.new(), run.id, self._clock.now())
            )
            uow.run_skill_bindings.add_all(bindings)
            uow.commit()
            return bindings

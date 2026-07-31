from __future__ import annotations

from friday.application.errors import EntityConflict
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    Skill,
    SkillId,
    SkillRevision,
    SkillRevisionId,
    SkillRevisionSourceKind,
    SkillStatus,
)


class SkillNotFound(EntityConflict):
    pass


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
                raise SkillNotFound("skill not found")
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
            skill, revision = uow.skills.get(skill_id), uow.skill_revisions.get(revision_id)
            if skill is None or revision is None:
                raise SkillNotFound("skill or revision not found")
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
                raise SkillNotFound("skill not found")
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

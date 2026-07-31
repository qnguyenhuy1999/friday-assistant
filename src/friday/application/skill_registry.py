from __future__ import annotations

from friday.application.errors import EntityConflict, SkillNotFound, SkillRevisionNotFound
from friday.application.ports import Clock, UnitOfWorkFactory
from friday.domain import (
    Skill,
    SkillId,
    SkillRevision,
    SkillRevisionId,
    SkillRevisionSourceKind,
    SkillStatus,
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

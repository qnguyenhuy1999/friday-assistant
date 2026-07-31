from __future__ import annotations

import hashlib

import pytest

from friday.application.errors import EntityConflict
from friday.application.skill_registry import (
    ActivateSkillRevision,
    ArchiveSkill,
    CreateSkill,
    CreateSkillRevision,
)
from friday.domain.errors import DomainValidationError
from friday.domain.skill import SkillRevisionSourceKind, SkillStatus
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def test_revision_lifecycle_is_explicit_and_immutable() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="coding.fix-ci", display_name="Fix CI", description=""
    )
    one = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="Repair tests", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    assert (
        skill.active_revision_id is None
        and one.content_sha256 == hashlib.sha256(b"Repair tests").hexdigest()
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=one.id)
    two = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="Repair focused tests",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    assert (
        one.version == 1
        and one.instructions == "Repair tests"
        and skill.active_revision_id == one.id
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=two.id)
    assert skill.active_revision_id == two.id and uow.skill_revision_repo.get(one.id) == one


def test_cross_skill_activation_and_archive_fail_closed() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    a = CreateSkill(factory, clock).execute(key="research.deep", display_name="A", description="")
    b = CreateSkill(factory, clock).execute(key="research.broad", display_name="B", description="")
    rev = CreateSkillRevision(factory, clock).execute(
        skill_id=b.id, instructions="Read", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    with pytest.raises(DomainValidationError):
        ActivateSkillRevision(factory, clock).execute(skill_id=a.id, revision_id=rev.id)
    ArchiveSkill(factory, clock).execute(a.id)
    assert a.status is SkillStatus.ARCHIVED
    with pytest.raises(EntityConflict):
        CreateSkillRevision(factory, clock).execute(
            skill_id=a.id, instructions="No", source_kind=SkillRevisionSourceKind.OPERATOR
        )


@pytest.mark.parametrize("content", ["", "bad\x00", "\ud800"])
def test_instruction_content_is_validated(content: str) -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="research.unicode", display_name="Unicode", description=""
    )
    with pytest.raises(DomainValidationError):
        CreateSkillRevision(factory, clock).execute(
            skill_id=skill.id, instructions=content, source_kind=SkillRevisionSourceKind.OPERATOR
        )
    assert (
        CreateSkillRevision(factory, clock)
        .execute(
            skill_id=skill.id,
            instructions="Đọc 日本語",
            source_kind=SkillRevisionSourceKind.OPERATOR,
        )
        .version
        == 1
    )

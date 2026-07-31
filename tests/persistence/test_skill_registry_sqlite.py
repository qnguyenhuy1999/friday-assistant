"""Real SQLite proofs for the Step 1 persistence invariants.

The reviewer's F1 finding is exactly that the application/domain check is not a
durable fence: the database must refuse a skills.active_revision_id that points
nowhere or to another skill's revision. These tests exercise the migrated
schema (Alembic head, not `create_all`) through raw SQL — deliberately
bypassing the domain check — so a passing suite proves the DB is the final
fence. The remaining tests cover the other Step 1 persistence invariants the
review called out: duplicate skill keys, the (skill_id, version) race, and the
immutability of a persisted v1 across later revisions and activation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from friday.application.errors import EntityConflict
from friday.application.skill_registry import (
    ActivateSkillRevision,
    CreateSkill,
    CreateSkillRevision,
)
from friday.domain.identifiers import SkillRevisionId
from friday.domain.skill import SkillRevision, SkillRevisionSourceKind, SkillStatus
from friday.infrastructure.persistence.database import create_engine, create_session_factory
from friday.infrastructure.persistence.repositories import (
    SkillRevisionRepository,
)
from friday.infrastructure.persistence.unit_of_work import create_unit_of_work_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
AT = "2026-01-02 03:00:00"


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 1, 2, 3, tzinfo=UTC)


def _migrated_engine(tmp_path: Path):
    db_path = tmp_path / "skill-registry.db"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{db_path}")


def _insert_skill(engine, skill_id: str, key: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO skills VALUES (:id, :key, 'n', '', 'active', NULL, :at, :at)"),
            {"id": skill_id, "key": key, "at": AT},
        )


def _insert_revision(engine, revision_id: str, skill_id: str, version: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO skill_revisions "
                "(id, skill_id, version, instructions, content_sha256, source_kind, created_at) "
                "VALUES (:id, :skill_id, :version, :instructions, :sha, 'operator', :at)"
            ),
            {
                "id": revision_id,
                "skill_id": skill_id,
                "version": version,
                "instructions": f"revision {version}",
                "sha": "a" * 64,
                "at": AT,
            },
        )


def _try_set_active(engine, skill_id: str, revision_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE skills SET active_revision_id = :revision_id WHERE id = :skill_id"),
            {"skill_id": skill_id, "revision_id": revision_id},
        )


def test_active_pointer_to_nonexistent_revision_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        _insert_skill(engine, "00000000-0000-0000-0000-000000000001", "fence.missing")
        with pytest.raises(IntegrityError):
            _try_set_active(
                engine,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-0000000000ff",
            )
    finally:
        engine.dispose()


def test_active_pointer_to_another_skills_revision_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        _insert_skill(engine, "00000000-0000-0000-0000-000000000001", "fence.a")
        _insert_skill(engine, "00000000-0000-0000-0000-000000000002", "fence.b")
        _insert_revision(
            engine,
            "00000000-0000-0000-0000-000000000003",
            "00000000-0000-0000-0000-000000000002",
            1,
        )
        with pytest.raises(IntegrityError):
            _try_set_active(
                engine,
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000003",
            )
    finally:
        engine.dispose()


def test_active_pointer_to_own_revision_succeeds(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        _insert_skill(engine, "00000000-0000-0000-0000-000000000001", "fence.valid")
        _insert_revision(
            engine,
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000001",
            1,
        )
        _try_set_active(
            engine,
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
        with engine.connect() as connection:
            active = connection.execute(
                text("SELECT active_revision_id FROM skills WHERE id = :skill_id"),
                {"skill_id": "00000000-0000-0000-0000-000000000001"},
            ).scalar_one()
        assert active == "00000000-0000-0000-0000-000000000002"
    finally:
        engine.dispose()


def test_duplicate_skill_key_is_rejected_by_persistence(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        CreateSkill(factory, Clock()).execute(key="fence.key", display_name="A", description="")
        with pytest.raises(EntityConflict):
            CreateSkill(factory, Clock()).execute(key="fence.key", display_name="B", description="")
    finally:
        engine.dispose()


def test_revision_version_race_loser_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    factory = create_session_factory(engine)
    try:
        with factory():
            skill = CreateSkill(create_unit_of_work_factory(factory), Clock()).execute(
                key="fence.race", display_name="R", description=""
            )
            skill_id = skill.id

        session_a, session_b = factory(), factory()
        try:
            version_a = SkillRevisionRepository(session_a).next_version(skill_id)
            version_b = SkillRevisionRepository(session_b).next_version(skill_id)
            assert version_a == version_b == 1
            revision_a = SkillRevision.new(
                id=SkillRevisionId.new(),
                skill_id=skill_id,
                version=version_a,
                instructions="first",
                source_kind=SkillRevisionSourceKind.OPERATOR,
                created_at=Clock().now(),
            )
            SkillRevisionRepository(session_a).add(revision_a)
            session_a.commit()

            loser = SkillRevision.new(
                id=SkillRevisionId.new(),
                skill_id=skill_id,
                version=version_b,
                instructions="second",
                source_kind=SkillRevisionSourceKind.OPERATOR,
                created_at=Clock().now(),
            )
            with pytest.raises(IntegrityError):
                SkillRevisionRepository(session_b).add(loser)
                session_b.commit()
        finally:
            session_a.close()
            session_b.close()
    finally:
        engine.dispose()


def test_persisted_v1_is_immutable_across_v2_and_activation(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    try:
        factory = create_unit_of_work_factory(create_session_factory(engine))
        clock = Clock()
        skill = CreateSkill(factory, clock).execute(
            key="fence.immutable", display_name="I", description=""
        )
        v1 = CreateSkillRevision(factory, clock).execute(
            skill_id=skill.id,
            instructions="original v1 content",
            source_kind=SkillRevisionSourceKind.OPERATOR,
        )
        v2 = CreateSkillRevision(factory, clock).execute(
            skill_id=skill.id,
            instructions="replacement v2 content",
            source_kind=SkillRevisionSourceKind.OPERATOR,
        )
        ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=v2.id)

        with factory() as uow:
            reloaded_v1 = uow.skill_revisions.get(v1.id)
            reloaded_skill = uow.skills.get(skill.id)
            assert reloaded_v1 is not None
            assert reloaded_skill is not None
            assert reloaded_v1.instructions == "original v1 content"
            assert reloaded_v1.version == 1
            assert reloaded_skill.active_revision_id == v2.id
            assert reloaded_skill.status is SkillStatus.ACTIVE
    finally:
        engine.dispose()

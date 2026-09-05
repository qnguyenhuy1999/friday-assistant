from __future__ import annotations

import hashlib

import pytest

from friday.application.commands import CreateTaskCommand
from friday.application.create_task import CreateTask
from friday.application.errors import EntityConflict, SkillNotFound, SkillRevisionNotFound
from friday.application.skill_registry import (
    ActivateSkillRevision,
    ArchiveSkill,
    CreateSkill,
    CreateSkillRevision,
    GetSkill,
    ReplaceTaskSkills,
)
from friday.application.skill_usage import AddSkillRunFeedback, MaterializeSkillUsage
from friday.domain.errors import DomainValidationError
from friday.domain.identifiers import RunId, SkillId, SkillRevisionId
from friday.domain.run import Run
from friday.domain.skill import SkillRevision, SkillRevisionSourceKind, SkillStatus
from friday.domain.skill_usage import SkillFeedbackRating, SkillUsageOutcome
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork
from tests.application.resolve_helpers import resolve_run_skills_without_claim


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


def test_missing_skill_and_revision_raise_not_found_errors() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    with pytest.raises(SkillNotFound):
        CreateSkillRevision(factory, clock).execute(
            skill_id=SkillId.new(),
            instructions="Nope",
            source_kind=SkillRevisionSourceKind.OPERATOR,
        )
    with pytest.raises(SkillNotFound):
        GetSkill(factory).execute(SkillId.new())
    skill = CreateSkill(factory, clock).execute(
        key="research.notfound", display_name="N", description=""
    )
    with pytest.raises(SkillNotFound):
        GetSkill(factory).list_revisions(SkillId.new())
    with pytest.raises(SkillRevisionNotFound):
        ActivateSkillRevision(factory, clock).execute(
            skill_id=skill.id, revision_id=SkillRevisionId.new()
        )


def test_get_revision_uses_exact_lookup_without_enumerating_history() -> None:
    class ExactRevisionRepository:
        def __init__(self, revision: SkillRevision) -> None:
            self.revision = revision
            self.get_calls = 0

        def get(self, revision_id: SkillRevisionId) -> SkillRevision | None:
            self.get_calls += 1
            return self.revision if revision_id == self.revision.id else None

        def list_for_skill(self, skill_id: SkillId) -> list[SkillRevision]:
            raise AssertionError("exact revision lookup must not enumerate history")

    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="research.exact-lookup", display_name="Exact", description=""
    )
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id,
        instructions="Exact persisted instructions",
        source_kind=SkillRevisionSourceKind.OPERATOR,
    )
    repository = ExactRevisionRepository(revision)
    uow.skill_revision_repo = repository  # type: ignore[assignment]

    assert GetSkill(factory).get_revision(skill.id, revision.id) is revision
    assert repository.get_calls == 1


def test_task_bindings_freeze_active_revision_and_new_retry_resolves_current_state() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task_id = CreateTask(factory, clock).execute(CreateTaskCommand("T", "")).task_id
    skill = CreateSkill(factory, clock).execute(key="review.pr", display_name="R", description="")
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="use v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=v1.id)
    ReplaceTaskSkills(factory, clock).execute(task_id=task_id, skill_ids=[skill.id])
    run = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
    uow.runs.add(run)

    frozen = resolve_run_skills_without_claim(factory, clock, run.id)
    assert [(x.skill_id, x.revision_id, x.position) for x in frozen] == [(skill.id, v1.id, 1)]
    assert uow.run_skill_resolutions.get(run.id) is not None

    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="use v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=v2.id)
    assert resolve_run_skills_without_claim(factory, clock, run.id)[0].revision_id == v1.id

    retry = Run.new(
        id=RunId.new(), task_id=task_id, execution_id=run.execution_id, created_at=clock.now()
    )
    uow.runs.add(retry)
    # Retry lineage inheritance belongs to RetryFailedRun, which copies the
    # exact source freeze. A bare resolver must never scan sibling Runs.
    assert resolve_run_skills_without_claim(factory, clock, retry.id)[0].revision_id == v2.id


def test_task_binding_replacement_rejects_unresolvable_and_duplicate_skills() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task_id = CreateTask(factory, clock).execute(CreateTaskCommand("T", "")).task_id
    skill = CreateSkill(factory, clock).execute(
        key="review.empty", display_name="R", description=""
    )
    with pytest.raises(EntityConflict):
        ReplaceTaskSkills(factory, clock).execute(task_id=task_id, skill_ids=[skill.id])
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="x", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=revision.id)
    with pytest.raises(EntityConflict):
        ReplaceTaskSkills(factory, clock).execute(task_id=task_id, skill_ids=[skill.id, skill.id])


def test_terminal_frozen_run_materializes_idempotent_factual_usage_and_feedback() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    task_id = CreateTask(factory, clock).execute(CreateTaskCommand("T", "")).task_id
    skill = CreateSkill(factory, clock).execute(
        key="evidence.frozen", display_name="E", description=""
    )
    revision = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="facts", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    ActivateSkillRevision(factory, clock).execute(skill_id=skill.id, revision_id=revision.id)
    ReplaceTaskSkills(factory, clock).execute(task_id=task_id, skill_ids=[skill.id])
    run = Run.new(id=RunId.new(), task_id=task_id, created_at=clock.now())
    uow.runs.add(run)
    resolve_run_skills_without_claim(factory, clock, run.id)
    run.start(clock.now())
    run.succeed(clock.now())

    records = MaterializeSkillUsage(factory, clock).execute(run.id)
    assert len(records) == 1
    assert records[0].revision_id == revision.id
    assert records[0].outcome is SkillUsageOutcome.SUCCEEDED
    assert records[0].tool_call_count == records[0].approval_count == 0
    assert MaterializeSkillUsage(factory, clock).execute(run.id) == records

    feedback = AddSkillRunFeedback(factory, clock).execute(
        run_id=run.id,
        skill_id=skill.id,
        rating=SkillFeedbackRating.HARMFUL,
        note="not useful",
        created_by="operator",
    )
    assert feedback.revision_id == revision.id
    assert records[0].outcome is SkillUsageOutcome.SUCCEEDED


def test_no_claim_resolution_helper_is_not_importable_from_production_packages() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for base in ("src", "apps"):
        for path in (root / base).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tests"):
                    offenders.append(f"{path.relative_to(root)}: from {node.module} import")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("tests"):
                            offenders.append(f"{path.relative_to(root)}: import {alias.name}")
    assert not offenders, "production modules must never import test-only helpers:\n" + "\n".join(
        offenders
    )

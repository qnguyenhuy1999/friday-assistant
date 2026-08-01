from __future__ import annotations

from friday.application.skill_evaluation import (
    CompareSkillImprovementProposal,
    DeterministicEvaluatorRegistry,
    RunSkillEvaluation,
)
from friday.application.skill_improvement import (
    CreateSkillEvidenceSnapshot,
    CreateSkillImprovementProposal,
)
from friday.application.skill_promotion import (
    ApproveSkillPromotion,
    ApproveSkillRollback,
    RequestSkillPromotion,
    RequestSkillRollback,
)
from friday.application.skill_registry import CreateSkill, CreateSkillRevision
from friday.domain import (
    EvaluationSuiteStatus,
    SkillEvaluationCase,
    SkillEvaluationCaseId,
    SkillEvaluationSuite,
    SkillEvaluationSuiteId,
    SkillRevisionSourceKind,
)
from tests.application.fakes import CountingUnitOfWorkFactory, FakeClock, FakeUnitOfWork


def test_exact_approved_promotion_creates_and_activates_one_generated_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="promote.test", display_name="P", description=""
    )
    base = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="base", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(base, clock.now())
    suite = SkillEvaluationSuite(
        id=SkillEvaluationSuiteId.new(),
        skill_id=skill.id,
        name="suite",
        description="",
        status=EvaluationSuiteStatus.ACTIVE,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    case = SkillEvaluationCase(
        id=SkillEvaluationCaseId.new(),
        suite_id=suite.id,
        position=1,
        input="x",
        expected_properties={"value": "ok"},
        grading_kind="exact_match",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    uow.skill_evaluation_suites.add(suite)
    uow.skill_evaluation_cases.add(case)
    baseline = RunSkillEvaluation(factory, clock, DeterministicEvaluatorRegistry()).execute(
        suite_id=suite.id, revision_id=base.id, outputs={str(case.id): "bad"}
    )
    snapshot = CreateSkillEvidenceSnapshot(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        evidence={"items": [{"id": "e"}]},
    )
    proposal = CreateSkillImprovementProposal(factory, clock).execute(
        skill_id=skill.id,
        base_revision_id=base.id,
        trigger_kind="manual",
        evidence_snapshot_id=snapshot.id,
        evidence_snapshot_hash=snapshot.content_sha256,
        evidence_ids={"e"},
        generator_version="brain-candidate-generator-v2",
        raw_candidate=(
            '{"version":1,"proposed_instructions":"better","rationale":"r",'
            '"addressed_evidence_ids":["e"]}'
        ),
    )
    CompareSkillImprovementProposal(factory, clock, DeterministicEvaluatorRegistry()).execute(
        proposal_id=proposal.id,
        baseline_evaluation_run_id=baseline.id,
        candidate_outputs={str(case.id): "ok"},
    )
    request = RequestSkillPromotion(factory, clock).execute(proposal.id)
    completed = ApproveSkillPromotion(factory, clock).execute(request.id, "operator")
    assert completed.promoted_revision_id is not None
    promoted = uow.skill_revision_repo.get(completed.promoted_revision_id)
    assert promoted is not None and promoted.version == 2
    stored_skill = uow.skill_repo.get(skill.id)
    assert stored_skill is not None and stored_skill.active_revision_id == promoted.id


def test_approved_rollback_reactivates_history_without_creating_a_revision() -> None:
    uow, clock = FakeUnitOfWork(), FakeClock()
    factory = CountingUnitOfWorkFactory(uow)
    skill = CreateSkill(factory, clock).execute(
        key="rollback.test", display_name="R", description=""
    )
    v1 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v1", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    v2 = CreateSkillRevision(factory, clock).execute(
        skill_id=skill.id, instructions="v2", source_kind=SkillRevisionSourceKind.OPERATOR
    )
    skill.activate(v2, clock.now())
    uow.skill_repo.save(skill)

    request = RequestSkillRollback(factory, clock).execute(
        skill_id=skill.id, target_revision_id=v1.id, reason="restore known behavior"
    )
    completed = ApproveSkillRollback(factory, clock).execute(request.id, "operator")

    assert completed.status.value == "completed"
    stored = uow.skill_repo.get(skill.id)
    assert stored is not None and stored.active_revision_id == v1.id
    assert uow.skill_revisions.list_for_skill(skill.id) == [v1, v2]
